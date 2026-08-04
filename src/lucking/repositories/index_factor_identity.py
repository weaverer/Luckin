"""指数身份解析与自举注册 Repository（data-model.md §2、§5 步骤 1）。

合法 ts_code 后缀（.SH/.SZ/.CSI/.SI）首次出现即幂等注册
``index_current`` + ``index_provider_mapping``；非法后缀或空代码返回 None，
由服务层记录 ``UNKNOWN_INDEX_IDENTITY`` 问题并跳过该条（spec ED-004）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.index_factor import IndexCurrent, IndexProviderMapping

# 部署账户实测（2026-08-02，20260731 全量 3146 行）后缀全集；
# 来源新增后缀时按 UNKNOWN_INDEX_IDENTITY 问题可见并可白名单扩展。
INDEX_CODE_SUFFIXES = frozenset(
    {".SH", ".SZ", ".CSI", ".SI", ".CI", ".NH", ".BJ", ".CNI"}
)


@dataclass(frozen=True, slots=True)
class ResolvedIndexIdentity:
    index_id: str
    index_code: str


def is_supported_index_code(provider_security_id: str) -> bool:
    return provider_security_id.endswith(tuple(INDEX_CODE_SUFFIXES)) and len(
        provider_security_id
    ) > len(".SI")


class IndexFactorIdentityRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def resolve_index_identity(
        self, *, provider_code: str, provider_security_id: str
    ) -> ResolvedIndexIdentity | None:
        """按来源标识解析规范指数身份；合法则自举注册，非法返回 None。"""
        if not is_supported_index_code(provider_security_id):
            return None
        with self._session_factory() as session:
            mapped = session.scalar(
                select(IndexProviderMapping).where(
                    IndexProviderMapping.provider_code == provider_code,
                    IndexProviderMapping.provider_security_id == provider_security_id,
                )
            )
            if mapped is not None:
                return ResolvedIndexIdentity(mapped.index_id, provider_security_id)
        try:
            return self._register(provider_code, provider_security_id)
        except IntegrityError:
            # 并发注册竞争：唯一约束兜底后重读
            with self._session_factory() as session:
                mapped = session.scalar(
                    select(IndexProviderMapping).where(
                        IndexProviderMapping.provider_code == provider_code,
                        IndexProviderMapping.provider_security_id == provider_security_id,
                    )
                )
                if mapped is None:
                    raise
                return ResolvedIndexIdentity(mapped.index_id, provider_security_id)

    def _register(self, provider_code: str, provider_security_id: str) -> ResolvedIndexIdentity:
        index_id = str(uuid4())
        now = datetime.now(UTC).replace(tzinfo=None)
        with self._session_factory.begin() as session:
            session.add(
                IndexCurrent(
                    index_id=index_id,
                    index_code=provider_security_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                IndexProviderMapping(
                    provider_code=provider_code,
                    provider_security_id=provider_security_id,
                    index_id=index_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        return ResolvedIndexIdentity(index_id, provider_security_id)
