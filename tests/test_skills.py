import pytest

from jobboard.skills import detect_level, extract_skills, strip_html


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Python 3 / FastAPI engineer", ["python", "fastapi"]),
        ("Postgres and PostgreSQL", ["postgresql"]),
        ("JavaScript developer", ["javascript"]),
        ("Java and JavaScript developer", ["javascript", "java"]),
        ("Going to the office", []),
        ("Go language developer", ["go"]),
        ("R language with RStudio", ["r"]),
        ("An R in a sentence is not enough", []),
        ("C# and C++", ["c++", "c#"]),
        ("LLM and large language models", ["llm"]),
        ("The role involves sql, but not NoSQL-only text", ["sql"]),
        ("<p>React</p><script>Python</script>", ["react"]),
        ("AWSome platform", []),
        ("K8s, Docker, and Terraform", ["docker", "kubernetes", "terraform"]),
    ],
)
def test_extract_skills_word_boundaries(text, expected):
    assert extract_skills(text) == expected


def test_strip_html_decodes_entities_and_removes_markup():
    assert strip_html("<p>FastAPI &amp; Python</p>") == "FastAPI & Python"


@pytest.mark.parametrize(
    ("title", "source_level", "expected"),
    [
        ("Backend Engineer", "senior", "senior"),
        ("Senior Backend Engineer", None, "senior"),
        ("Graduate Developer", None, "junior"),
        ("Software Engineer", "mid-level", "mid"),
        ("Developer", None, "unknown"),
        ("Junior Developer", "principal", "senior"),
    ],
)
def test_detect_level(title, source_level, expected):
    assert detect_level(title, source_level) == expected
