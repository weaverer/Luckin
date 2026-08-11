from sqlalchemy.orm import Session, sessionmaker

from lucking.integrations.task_readers.market_data import MarketDataTaskReader


def stock_factor_reader(sessions: sessionmaker[Session]) -> MarketDataTaskReader:
    return MarketDataTaskReader(sessions, source_domains=frozenset({"stock-factor"}))
