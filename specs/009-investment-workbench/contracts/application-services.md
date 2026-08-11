# 内部服务契约：投资工作台

## 1. 边界

FastAPI 路由只负责协议解析、认证/CSRF、调用 Service 和响应映射。Service 不依赖 FastAPI、
Vue、Cookie、Tushare 或飞书字段；Repository 不执行权限决策以外的业务编排。

## 2. AuthService

```python
@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    user_id: str
    username: str
    display_name: str
    session_token: str
    csrf_token: str
    expires_at: datetime

class AuthService(Protocol):
    def login(self, username: str, password: str) -> AuthenticatedSession: ...
    def authenticate(self, session_token: str) -> AuthenticatedSession: ...
    def logout(self, session_token: str) -> None: ...
    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> None: ...
```

- 登录失败统一返回 `InvalidCredentials`，不得区分账号不存在、禁用或密码错误。
- 用户名规范化后再查询；登录失败按用户名摘要和客户端地址实施限流，不记录密码。
- 修改密码在 MySQL 事务更新哈希，提交后撤销用户全部 Redis 会话；撤销失败必须记录并告警。
- 管理命令 `create-user` 和 `disable-user` 使用同一密码策略与 Repository，不暴露 HTTP 注册接口。

## 3. CalendarWorkspaceService

```python
class CalendarWorkspaceService(Protocol):
    def list_calendar(
        self, user_id: str, market_code: str, start_date: date, end_date: date
    ) -> tuple[CalendarDayView, ...]: ...
    def create_important_date(
        self, user_id: str, command: ImportantDateInput
    ) -> ImportantDateView: ...
    def update_important_date(
        self, user_id: str, important_date_id: str, command: ImportantDateInput
    ) -> ImportantDateView: ...
    def delete_important_date(self, user_id: str, important_date_id: str) -> None: ...
```

- 日期范围最多 400 天；不存在交易日记录返回 `UNKNOWN`，不得推断为休市。
- 更新和删除必须以 `user_id + important_date_id` 定位；其他用户资源统一表现为不存在。
- 重要日标题按 `data-model.md` 规范化，并由唯一约束兜底并发重复。

## 4. StockWorkspaceService

```python
class StockWorkspaceService(Protocol):
    def list_stocks(self, query: StockListQuery) -> Page[StockSummary]: ...
    def get_stock(self, stock_id: str, as_of_date: date) -> StockDetail: ...
    def list_daily_quotes(
        self, stock_id: str, start_date: date | None, end_date: date | None, limit: int
    ) -> tuple[DailyQuoteView, ...]: ...
```

- 股票主数据来自现有 `StockListService`/Repository 规范模型；行情来自现有
  `MarketDataService`/ClickHouse Repository，不调用 Provider。
- `stock_id` 是跨 MySQL/ClickHouse 的唯一规范身份；不允许前端提交 Provider ID。
- 行情区间默认最近 120 条，最大 400 条，按 `trade_date` 升序；停牌日不合成记录。
- 最新行情早于最近一个已知交易日时标记 `STALE`；不存在时标记 `MISSING`。

## 5. WatchlistService

```python
class WatchlistService(Protocol):
    def list_groups(self, user_id: str) -> tuple[WatchlistGroupView, ...]: ...
    def create_group(self, user_id: str, name: str, sort_order: int) -> WatchlistGroupView: ...
    def update_group(
        self, user_id: str, group_id: str, name: str, sort_order: int
    ) -> WatchlistGroupView: ...
    def delete_group(self, user_id: str, group_id: str) -> None: ...
    def add_member(self, user_id: str, group_id: str, stock_id: str) -> WatchlistMemberView: ...
    def remove_member(self, user_id: str, group_id: str, stock_id: str) -> None: ...
```

- 每次写入先验证分组所有权，再验证股票存在；相同成员重复添加返回 `Conflict`。
- 删除分组与成员清理在同一事务完成；不得删除或修改 `stock_current`。
- 所有列表按 `sort_order` 和业务 UUID 稳定排序。

## 6. BrokerRecommendationQueryService

```python
class BrokerRecommendationQueryService(Protocol):
    def list_recommendations(
        self, query: BrokerRecommendationQuery
    ) -> Page[BrokerRecommendationView]: ...
```

- 复用现有规范推荐和股票身份，不返回 Provider 字段、原始载荷或同步问题明细。
- `recommendation_month` 必须为月首；分页范围为 1–1000。
- 页面和公共 API 统一称为“推荐月份”，不得将该字段展示或描述为具体推荐日期。

## 7. 统一响应结构

```python
T = TypeVar("T")

@dataclass(frozen=True, slots=True)
class Pagination:
    limit: int
    offset: int
    total: int
    has_more: bool

@dataclass(frozen=True, slots=True)
class ErrorDetail:
    field: str | None
    code: str
    message: str

@dataclass(frozen=True, slots=True)
class PageData(Generic[T]):
    items: tuple[T, ...]
    pagination: Pagination

@dataclass(frozen=True, slots=True)
class ApiResponse(Generic[T]):
    code: int
    message: str
    data: T | None
    errors: tuple[ErrorDetail, ...]
    request_id: str
    timestamp: datetime
```

- 成功响应必须满足 `code=0`、`message=""`、`errors=[]` 且 `data` 符合端点 DTO。
- 失败响应保持 4xx/5xx HTTP 状态，并满足 `code!=0`、`data=null`；`message` 为安全中文摘要，
  `errors` 保存字段或可操作明细。HTTP 状态和业务码各自断言，不互相替代。
- 分页列表的 `data` 为 `PageData[T]`，`total/limit/offset/has_more` 不出现在顶层。
- `request_id` 在请求入口生成或接收可信代理值并贯穿日志；`timestamp` 为响应生成时的 UTC ISO 8601 时间。
- HTTP 204 无响应体；其余成功与失败响应都使用 `ApiResponse`。
- 前端生成类型按具体 `data` 建立 `AuthSessionResponse`、`StockListResponse` 等强类型，
  不以 `dict[str, Any]` 或 TypeScript `any` 作为公共边界。
- 稳定业务码以 OpenAPI `BusinessErrorCode` 为唯一登记表：`100001/100002/100003` 分别表示
  登录凭据、会话、CSRF 错误；`200001/200002/200003` 分别表示请求校验、查询范围、密码策略错误；
  `300001` 表示资源不存在或无所有权；`400001`～`400004` 分别表示重要日、自选名称、成员重复、
  容量冲突；`500001/500002` 分别表示限流、外部依赖不可用；`900001` 表示未预期服务错误。

## 8. API 安全和错误映射

- 除 `/auth/login` 外所有 `/api/v1` 接口要求有效会话 Cookie。
- 所有写请求同时校验 `Origin`/`Referer` 与 `X-CSRF-Token`；前端路由守卫不替代后端认证。
- 领域 `ValidationError/Conflict/NotFound/InvalidCredentials/RateLimited/DependencyUnavailable` 分别映射为
  400/409/404/401/429/503；意外错误返回通用 500。每个异常同时映射 OpenAPI 已登记的稳定整数业务码；
  安全中文提示放在顶层 `message`，结构化明细放在 `errors[]`。
- FastAPI 请求参数和请求体校验必须通过统一异常处理器映射为 HTTP 400 与 `200001`，日期或查询
  范围错误使用 `200002`，密码策略错误使用 `200003`，不得返回
  默认 422 响应结构；受保护端点、写端点和全部端点分别显式声明统一 401、403 和 500。
- API 错误不得包含 SQL、堆栈、密码哈希、Cookie、CSRF Token、webhook 或 Provider 原始响应。

## 9. 契约测试

- Memory Repository 和真实 MySQL 对账号、重要日、自选执行相同契约用例。
- Redis Session Store 覆盖过期、空闲刷新、退出、禁用和改密全撤销。
- 未认证、跨用户 UUID、缺少/错误 CSRF 和跨源写请求均被拒绝。
- OpenAPI 生成客户端后 TypeScript 构建通过，生成目录无手工修改。
- 成功、字段校验、认证、授权、冲突、限流和意外错误均验证 HTTP 状态与业务码；
  分页信息只出现在 `data.pagination`，所有有响应体结果都含 `request_id/timestamp`。
- 股票、行情和金股查询证明不调用第三方 Provider，且不返回任何供应商字段。
