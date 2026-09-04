import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# alembic/env.py loyiha ildizidan (MedFlow/) chaqirilmasligi ham mumkin
# (masalan boshqa working directory'dan `alembic upgrade head`), shuning
# uchun loyiha ildizini sys.path'ga qo'shamiz — aks holda quyidagi
# `import database` va `import models` ishlamay qoladi.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Loyihaning o'zidagi bitta haqiqiy manba (single source of truth):
# database.py (DATABASE_URL, .env'dan) va models.py (Base.metadata).
# Bu ikkalasini qo'lda alembic.ini/env.py'da qaytadan yozish o'rniga shu
# yerdan olamiz — shunda migratsiya doim ilova ishlatadigan bazaning aynan
# o'zi va aynan shu modellar bilan ishlaydi (dev'da SQLite, production'da
# .env orqali PostgreSQL'ga o'zgartirilganda ham hech narsani qo'lda
# sync qilish shart emas).
from database import Base, DATABASE_URL  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# alembic.ini'dagi sqlalchemy.url bo'sh qoldirilgan — uni shu yerda,
# ishga tushirish vaqtida, database.py'dan (demak .env'dagi
# DATABASE_URL'dan) to'ldiramiz.
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite ALTER TABLE'ni deyarli qo'llab-quvvatlamaydi (masalan,
        # ustun qo'shish/o'zgartirish uchun butun jadvalni qayta qurish
        # kerak). Alembic'ning "batch" rejimi buni avtomatik qiladi —
        # yangi jadval yaratadi, ma'lumotni ko'chiradi, eskisini almashtiradi.
        # PostgreSQL'da bu rejim shunchaki oddiy ALTER TABLE'ga tushadi,
        # shuning uchun ikkala baza uchun ham xavfsiz.
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # sabab: offline funksiyadagi izohga qarang
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
