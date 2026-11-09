"""Deterministic, word-boundary skill and seniority extraction."""

import re
from html.parser import HTMLParser

# Canonical skill name -> accepted spellings. Keep the aliases explicit to avoid
# substring matches such as Java inside JavaScript or "go" inside "going".
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "python": ("python", "python 2", "python 3"),
    "javascript": ("javascript", "java script", "js", "ecmascript"),
    "typescript": ("typescript", "type script", "ts"),
    "java": ("java",),
    "kotlin": ("kotlin",),
    "scala": ("scala",),
    "go": ("go", "golang", "go language"),
    "rust": ("rust",),
    "c": ("c language", "c programming"),
    "c++": ("c++", "cpp", "c plus plus"),
    "c#": ("c#", "c sharp", "c-sharp"),
    "php": ("php",),
    "ruby": ("ruby",),
    "elixir": ("elixir",),
    "erlang": ("erlang",),
    "swift": ("swift",),
    "objective-c": ("objective-c", "objective c", "objc"),
    "dart": ("dart",),
    "lua": ("lua",),
    "perl": ("perl",),
    "r": ("r language", "r programming", "rstudio", "tidyverse"),
    "bash": ("bash", "shell scripting", "shell script"),
    "powershell": ("powershell", "power shell"),
    "sql": ("sql",),
    "postgresql": ("postgresql", "postgres", "postgre sql", "psql"),
    "mysql": ("mysql",),
    "sqlite": ("sqlite",),
    "mongodb": ("mongodb", "mongo db"),
    "redis": ("redis",),
    "dynamodb": ("dynamodb", "dynamo db"),
    "elasticsearch": ("elasticsearch", "elastic search"),
    "opensearch": ("opensearch", "open search"),
    "clickhouse": ("clickhouse", "click house"),
    "snowflake": ("snowflake",),
    "bigquery": ("bigquery", "big query"),
    "dbt": ("dbt",),
    "apache spark": ("apache spark", "pyspark", "spark sql"),
    "kafka": ("kafka", "apache kafka"),
    "apache flink": ("apache flink", "flink"),
    "airflow": ("airflow", "apache airflow"),
    "pandas": ("pandas",),
    "numpy": ("numpy",),
    "pytorch": ("pytorch", "py torch"),
    "tensorflow": ("tensorflow", "tensor flow"),
    "keras": ("keras",),
    "scikit-learn": ("scikit-learn", "scikit learn", "sklearn"),
    "xgboost": ("xgboost", "xg boost"),
    "langchain": ("langchain", "lang chain"),
    "llamaindex": ("llamaindex", "llama index", "llama-index"),
    "llm": ("llm", "large language model", "large language models"),
    "generative ai": ("generative ai", "genai", "gen ai"),
    "prompt engineering": ("prompt engineering", "prompt design"),
    "rag": ("rag", "retrieval augmented generation", "retrieval-augmented generation"),
    "vector database": (
        "vector database",
        "vector databases",
        "vector db",
        "vector store",
    ),
    "embeddings": ("embedding", "embeddings", "text embedding"),
    "openai": ("openai", "open ai"),
    "anthropic": ("anthropic", "claude api"),
    "hugging face": ("hugging face", "huggingface"),
    "transformers": ("transformers", "transformer models"),
    "fastapi": ("fastapi", "fast api"),
    "django": ("django",),
    "flask": ("flask",),
    "streamlit": ("streamlit", "stream lit"),
    "react": ("react", "react.js", "reactjs"),
    "next.js": ("next.js", "nextjs", "next js"),
    "vue": ("vue", "vue.js", "vuejs"),
    "angular": ("angular", "angularjs"),
    "svelte": ("svelte", "sveltekit"),
    "node.js": ("node.js", "nodejs", "node js"),
    "express": ("express.js", "expressjs", "express js"),
    "nestjs": ("nestjs", "nest.js", "nest js"),
    "html": ("html", "html5"),
    "css": ("css", "css3"),
    "tailwind css": ("tailwind", "tailwind css"),
    "graphql": ("graphql", "graph ql"),
    "rest api": ("rest api", "restful api", "restful services"),
    "grpc": ("grpc", "g-rpc"),
    "docker": ("docker", "docker compose", "docker-compose"),
    "kubernetes": ("kubernetes", "k8s"),
    "terraform": ("terraform",),
    "aws": ("aws", "amazon web services"),
    "azure": ("azure", "microsoft azure"),
    "gcp": ("gcp", "google cloud platform"),
    "linux": ("linux",),
    "git": ("git", "git version control"),
    "github actions": ("github actions", "github workflow"),
    "jenkins": ("jenkins",),
    "ci/cd": ("ci/cd", "continuous integration", "continuous delivery"),
    "prometheus": ("prometheus",),
    "grafana": ("grafana",),
    "oauth": ("oauth", "oauth2", "oauth 2.0"),
    "pytest": ("pytest", "py.test"),
}


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.lower() in {"script", "style"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


def strip_html(value: str) -> str:
    parser = _VisibleText()
    parser.feed(value or "")
    parser.close()
    return " ".join(parser.parts)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE)


_PATTERNS = {
    canonical: tuple(_alias_pattern(alias) for alias in aliases)
    for canonical, aliases in SKILL_ALIASES.items()
}


def extract_skills(*parts: str) -> list[str]:
    """Return distinct canonical skills in curated-dictionary order."""
    text = strip_html(" ".join(part for part in parts if part))
    return [
        canonical
        for canonical, patterns in _PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    ]


_LEVEL_PATTERNS = {
    "senior": re.compile(
        r"\b(senior|sr\.?|staff|principal|lead|chief)\b", re.IGNORECASE
    ),
    "junior": re.compile(
        r"\b(junior|jr\.?|entry[ -]?level|graduate|intern)\b", re.IGNORECASE
    ),
    "mid": re.compile(r"\b(mid[ -]?level|intermediate|midweight)\b", re.IGNORECASE),
}


def detect_level(title: str = "", source_level: str | None = None) -> str:
    """Normalize a source seniority field, then fall back to title keywords."""
    if source_level:
        normalized = source_level.strip().lower().replace("_", " ").replace("-", " ")
        if normalized in {"junior", "jr", "entry", "entry level", "intern", "graduate"}:
            return "junior"
        if normalized in {"mid", "mid level", "intermediate", "midweight"}:
            return "mid"
        if normalized in {"senior", "sr", "staff", "principal", "lead", "expert"}:
            return "senior"
    for level in ("senior", "junior", "mid"):
        if _LEVEL_PATTERNS[level].search(title or ""):
            return level
    return "unknown"
