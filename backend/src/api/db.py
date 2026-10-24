import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from backend.src.api.config import settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(s: str) -> str:
    s = s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:30]


# ---------- engine ----------
engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"timeout": 10})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")      # worker can write while the API reads
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(engine, expire_on_commit=False)


# ---------- models ----------
class Base(DeclarativeBase):
    pass


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    from_folder: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)
    docs: Mapped[list["Doc"]] = relationship(back_populates="batch", order_by="Doc.slug")


class Doc(Base):
    __tablename__ = "docs"
    __table_args__ = (Index("docs_status", "status", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(unique=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"))
    dir: Mapped[str]                                   # relative to DATA_DIR: <batch_slug>/<doc_slug>
    filename: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")   # queued|processing|review|done|error
    flags: Mapped[int] = mapped_column(default=0)           # open flags (denormalised for the list view)
    error: Mapped[str | None]
    updated_at: Mapped[str] = mapped_column(default=now)
    batch: Mapped[Batch] = relationship(back_populates="docs")
    flag_items: Mapped[list["Flag"]] = relationship(order_by="Flag.id", cascade="all, delete-orphan")


class Flag(Base):
    __tablename__ = "flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("docs.id"))
    row_idx: Mapped[int]
    col_idx: Mapped[int]
    kind: Mapped[str]                                  # bad | warn
    msg: Mapped[str]
    why: Mapped[str | None]
    suggestion: Mapped[str | None]
    done: Mapped[bool] = mapped_column(default=False)


class Correction(Base):
    """Human edits. Machine output is never overwritten."""
    __tablename__ = "corrections"
    doc_id: Mapped[int] = mapped_column(ForeignKey("docs.id"), primary_key=True)
    row_idx: Mapped[int] = mapped_column(primary_key=True)
    col_idx: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str]


def init() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)       # swap for Alembic migrations once the schema settles


def get_db():  # FastAPI dependency
    with SessionLocal() as db:
        yield db
