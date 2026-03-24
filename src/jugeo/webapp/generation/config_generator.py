"""Config and Dockerfile generation — stdlib only."""
from __future__ import annotations

import secrets
import textwrap
from .models import AppSpec, ConfigSpec, ModelSpec


class ConfigCodeGenerator:
    """Produces Flask config module source strings."""

    def generate_config_module(self, config: ConfigSpec) -> str:
        secret = config.secret_key or self.generate_secret_key()
        db_url = config.database_url or "sqlite:///app.db"
        debug = config.debug
        custom_lines = "\n".join(
            f"    {k.upper()} = {v!r}" for k, v in config.custom_config.items()
        )
        if custom_lines:
            custom_lines = "\n" + custom_lines

        return textwrap.dedent(f"""\
import os


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', '{secret}')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', '{db_url}')
    SQLALCHEMY_TRACK_MODIFICATIONS = False{custom_lines}


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///dev.db')


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


config_by_name = {{
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}}
""")

    def generate_env_template(self, config: ConfigSpec) -> str:
        return textwrap.dedent(f"""\
# Environment configuration
SECRET_KEY=change-me-in-production
DATABASE_URL={config.database_url or 'sqlite:///app.db'}
FLASK_ENV=development
FLASK_DEBUG=1
""")

    def generate_secret_key(self) -> str:
        return secrets.token_hex(32)

    def generate_database_config(self, models: list) -> str:
        model_names = ", ".join(m.name if hasattr(m, "name") else str(m) for m in models)
        return textwrap.dedent(f"""\
# Database configuration
# Models: {model_names}
import os

DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'app.db')
DATABASE_URL = f'sqlite:///{{DATABASE_PATH}}'
""")


class DockerfileGenerator:
    """Produces Dockerfile and docker-compose.yml content."""

    def generate_dockerfile(self, spec: AppSpec) -> str:
        return textwrap.dedent(f"""\
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE {spec.port}

CMD ["python", "main.py"]
""")

    def generate_docker_compose(self, spec: AppSpec) -> str:
        return textwrap.dedent(f"""\
version: '3.8'

services:
  web:
    build: .
    ports:
      - "{spec.port}:{spec.port}"
    environment:
      - FLASK_ENV=production
      - SECRET_KEY=${{SECRET_KEY:-change-me}}
    volumes:
      - app-data:/app/data
    restart: unless-stopped

volumes:
  app-data:
""")
