"""集中配置：全部来自环境变量，启动时校验，缺失即快速失败。

真实密钥只出现在 .env 或系统环境变量里，绝不硬编码、绝不进版本库。
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    APP_NAME: str = "Sales Insight"
    APP_ENV: Literal["dev", "prod"] = "dev"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    # 生产必须显式列出来源，禁止通配符
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ---- Database ----
    # 本机默认 SQLite；切 PostgreSQL 只需改这一行，业务代码不动
    DATABASE_URL: str = "sqlite:///./sales_insight.db"
    SQL_ECHO: bool = False

    # ---- Auth ----
    JWT_SECRET: str = Field(..., min_length=16, description="至少 16 位随机串，生产必须更换")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MIN: int = 30
    REFRESH_TOKEN_TTL_DAYS: int = 7

    # ---- AI (DeepSeek / OpenAI-compatible) ----
    LLM_PROVIDER: Literal["deepseek", "ollama"] = "deepseek"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434/v1"
    OLLAMA_MODEL: str = "deepseek-r1:7b"
    LLM_TIMEOUT_SEC: int = 120
    LLM_MAX_RETRIES: int = 2

    # ---- Text2SQL safety ----
    TEXT2SQL_ENABLED: bool = True
    # 只允许出现在 SQL 里的表名（白名单），越权表一律拒绝执行
    TEXT2SQL_TABLES: list[str] = ["fact_sales", "dim_customer", "dim_product", "dim_region"]
    TEXT2SQL_ROW_LIMIT: int = 500

    # ---- RAG / 向量检索 ----
    RAG_ENABLED: bool = True

    # Milvus 连接串。两种模式共用同一套代码，只改这一行：
    #   lite（本地文件，免 Docker，默认）：./milvus.db
    #   standalone（Docker）：http://127.0.0.1:19530
    #
    # 刻意不叫 MILVUS_URI：pymilvus 3.x 读的就是这个环境变量名，
    # 且 import 时就用它初始化模块级单例并校验格式（只认 http://），
    # lite 的文件路径会被判非法直接抛异常 —— 叫 MILVUS_CONN 可以躲开这个冲突。
    MILVUS_CONN: str = "./milvus.db"
    MILVUS_TOKEN: str = ""
    MILVUS_COLLECTION: str = "sales_knowledge"

    # embedding 后端：local（内置哈希，零依赖）| bge（本地 BGE 模型）| api（OpenAI 兼容 /embeddings）
    EMBEDDING_BACKEND: Literal["local", "bge", "api"] = "local"
    EMBEDDING_DIM: int = 512
    EMBEDDING_MODEL: str = "bge-small-zh-v1.5"
    # BGE 模型本地缓存目录（sentence-transformers 会往这里下模型）
    EMBEDDING_MODEL_DIR: str = "./models"
    EMBEDDING_BASE_URL: str = "http://127.0.0.1:8080/v1"
    EMBEDDING_API_KEY: str = ""

    # 检索参数
    RAG_TOP_K: int = 5
    RAG_CANDIDATE_K: int = 20  # 混合检索时每路先各召回多少，再融合截断到 TOP_K
    RAG_HYBRID: bool = True  # BM25 + 向量混合；关掉则退化为纯向量检索
    RAG_RRF_K: int = 60  # RRF 平滑常数，业界默认 60
    RAG_CHUNK_SIZE: int = 320
    RAG_CHUNK_OVERLAP: int = 60
    RAG_SCORE_THRESHOLD: float = 0.0  # 默认不过滤，靠 top_k + 融合排序控制

    # ---- Alerts ----
    ALERT_DEDUPE_MINUTES: int = 60
    WEBHOOK_URL: str = ""
    WEBHOOK_TYPE: Literal["dingtalk", "wecom", "feishu", "none"] = "none"
    WEBHOOK_KEYWORD: str = "销售分析"

    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    ALERT_EMAIL_TO: str = ""

    # ---- Uploads ----
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_MB: int = 50
    # 知识库源文件目录。刻意放在 app 包内而不是 uploads/：
    # (1) 上传目录常被清理，源文件丢了就没法重建索引；
    # (2) 随代码一起分发，别人 clone 下来就有示例知识库可用。
    KB_SOURCE_DIR: str = "./app/kb_docs"

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalize_sqlite(cls, v: str) -> str:
        # SQLite 需要 file 路径形式，补一层保险
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    def validate_startup(self) -> None:
        """启动时集中校验，缺关键配置直接抛错，避免带着坏配置跑起来。"""
        if self.LLM_PROVIDER == "deepseek" and not self.DEEPSEEK_API_KEY:
            raise RuntimeError(
                "DEEPSEEK_API_KEY 未配置：请在 backend/.env 中填写，或把 LLM_PROVIDER 改成 ollama"
            )
        if self.EMBEDDING_BACKEND == "api" and not self.EMBEDDING_API_KEY:
            raise RuntimeError(
                "EMBEDDING_BACKEND=api 但 EMBEDDING_API_KEY 为空：请填写 Key，"
                "或把 EMBEDDING_BACKEND 改回 local"
            )
        if self.APP_ENV == "prod" and "*" in self.CORS_ORIGINS:
            raise RuntimeError("生产环境禁止使用 CORS 通配符")
        if self.APP_ENV == "prod" and self.JWT_SECRET.startswith("dev-only"):
            raise RuntimeError("生产环境必须更换 JWT_SECRET")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
