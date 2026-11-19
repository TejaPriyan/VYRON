"""Development seed data.

SEED DATA, NOT PRODUCTION FACTS. Every record carries ``Provenance.SEED`` and the UI must show it as
development data. The entities and relationships are a small, plausible sample for exercising the
graph, search and timeline; each relationship is a deliberately loose, human-curated statement and
none has been backed by recorded evidence. Replace with ingested, sourced data before launch.
"""

from __future__ import annotations

import uuid
from datetime import date

from vyron_api.adapters.memory_graph import InMemoryGraph
from vyron_api.domain.entities import Entity, EntityKind, Relationship, RelationType
from vyron_api.domain.enums import DatePrecision
from vyron_api.domain.provenance import SEED_NOTICE, Provenance
from vyron_api.domain.timeline import TimelineEvent

__all__ = [  # SEED_NOTICE is re-exported: its text lives with the provenance labels
    "SEED_NAMESPACE",
    "SEED_NOTICE",
    "build_seed_graph",
    "entity_id",
    "relationship_id",
    "seed_entities",
    "seed_relationships",
    "seed_timeline",
]
SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "seed.vyron.invalid")

K = EntityKind
R = RelationType

# (slug, kind, name, aliases)
_ENTITIES: tuple[tuple[str, EntityKind, str, tuple[str, ...]], ...] = (
    ("python", K.LANGUAGE, "Python", ()),
    ("javascript", K.LANGUAGE, "JavaScript", ("JS", "ECMAScript")),
    ("typescript", K.LANGUAGE, "TypeScript", ("TS",)),
    ("react", K.LIBRARY, "React", ("React.js", "ReactJS")),
    ("next-js", K.FRAMEWORK, "Next.js", ("Next", "NextJS")),
    ("node-js", K.PLATFORM, "Node.js", ("Node", "NodeJS")),
    ("postgresql", K.DATABASE, "PostgreSQL", ("Postgres",)),
    ("neo4j", K.DATABASE, "Neo4j", ()),
    ("docker", K.TECHNOLOGY, "Docker", ()),
    ("kubernetes", K.TECHNOLOGY, "Kubernetes", ("K8s",)),
    ("pytorch", K.FRAMEWORK, "PyTorch", ()),
    ("tensorflow", K.FRAMEWORK, "TensorFlow", ("TF",)),
    ("numpy", K.LIBRARY, "NumPy", ()),
    ("cuda", K.PLATFORM, "CUDA", ()),
    ("github", K.PLATFORM, "GitHub", ()),
    ("vercel", K.COMPANY, "Vercel", ()),
    ("nvidia", K.COMPANY, "NVIDIA", ()),
    # Languages
    ("rust", K.LANGUAGE, "Rust", ()),
    ("go", K.LANGUAGE, "Go", ("Golang",)),
    ("c", K.LANGUAGE, "C", ()),
    ("cpp", K.LANGUAGE, "C++", ("CPP",)),
    ("java", K.LANGUAGE, "Java", ()),
    ("swift", K.LANGUAGE, "Swift", ()),
    ("kotlin", K.LANGUAGE, "Kotlin", ()),
    # Databases & Storage
    ("redis", K.DATABASE, "Redis", ()),
    ("sqlite", K.DATABASE, "SQLite", ()),
    ("mongodb", K.DATABASE, "MongoDB", ("Mongo",)),
    ("mysql", K.DATABASE, "MySQL", ()),
    ("clickhouse", K.DATABASE, "ClickHouse", ()),
    # AI & ML Ecosystem
    ("openai", K.COMPANY, "OpenAI", ()),
    ("anthropic", K.COMPANY, "Anthropic", ()),
    ("huggingface", K.COMPANY, "Hugging Face", ("HF",)),
    ("gpt-4", K.AI_MODEL, "GPT-4", ()),
    ("llama", K.AI_MODEL, "Llama", ("Llama 3",)),
    ("claude", K.AI_MODEL, "Claude", ()),
    ("transformers", K.LIBRARY, "Transformers", ()),
    ("pandas", K.LIBRARY, "pandas", ()),
    ("scikit-learn", K.LIBRARY, "scikit-learn", ("sklearn",)),
    ("jax", K.LIBRARY, "JAX", ()),
    # Core Systems & Cloud
    ("linux", K.PLATFORM, "Linux", ()),
    ("git", K.TECHNOLOGY, "Git", ()),
    ("nginx", K.TECHNOLOGY, "NGINX", ()),
    ("terraform", K.TECHNOLOGY, "Terraform", ()),
    ("aws", K.PLATFORM, "AWS", ("Amazon Web Services",)),
    ("webassembly", K.TECHNOLOGY, "WebAssembly", ("Wasm",)),
    # Web Frameworks & Tools
    ("vue", K.FRAMEWORK, "Vue.js", ("Vue",)),
    ("svelte", K.FRAMEWORK, "Svelte", ()),
    ("angular", K.FRAMEWORK, "Angular", ()),
    ("vite", K.TECHNOLOGY, "Vite", ()),
    ("tailwind-css", K.FRAMEWORK, "Tailwind CSS", ("Tailwind",)),
    ("django", K.FRAMEWORK, "Django", ()),
    ("fastapi", K.FRAMEWORK, "FastAPI", ()),
    ("flask", K.FRAMEWORK, "Flask", ()),
    ("graphql", K.TECHNOLOGY, "GraphQL", ()),
    # Tech Giants & Foundations
    ("google", K.COMPANY, "Google", ("Alphabet",)),
    ("microsoft", K.COMPANY, "Microsoft", ("MSFT",)),
    ("meta", K.COMPANY, "Meta", ("Facebook",)),
    ("apple", K.COMPANY, "Apple", ()),
    ("linux-foundation", K.ORGANIZATION, "Linux Foundation", ()),
    ("python-software-foundation", K.ORGANIZATION, "Python Software Foundation", ("PSF",)),
)

# (source slug, relation, target slug)
_RELATIONSHIPS: tuple[tuple[str, RelationType, str], ...] = (
    ("react", R.BUILT_ON, "javascript"),
    ("typescript", R.BUILT_ON, "javascript"),
    ("node-js", R.USES, "javascript"),
    ("next-js", R.BUILT_ON, "react"),
    ("next-js", R.USES, "node-js"),
    ("vercel", R.BUILDS, "next-js"),
    ("vercel", R.RELATED_TO, "github"),
    ("numpy", R.BUILT_ON, "python"),
    ("pytorch", R.USES, "python"),
    ("tensorflow", R.USES, "python"),
    ("tensorflow", R.DEPENDS_ON, "numpy"),
    ("pytorch", R.RELATED_TO, "numpy"),
    ("pytorch", R.USES, "cuda"),
    ("tensorflow", R.USES, "cuda"),
    ("nvidia", R.BUILDS, "cuda"),
    ("docker", R.RELATED_TO, "kubernetes"),
    ("neo4j", R.RELATED_TO, "postgresql"),
    # Systems & Cloud
    ("linux", R.BUILT_ON, "c"),
    ("git", R.BUILT_ON, "c"),
    ("git", R.RELATED_TO, "github"),
    ("linux-foundation", R.RELATED_TO, "linux"),
    ("linux-foundation", R.RELATED_TO, "kubernetes"),
    ("docker", R.BUILT_ON, "go"),
    ("kubernetes", R.BUILT_ON, "go"),
    ("kubernetes", R.CREATED_BY, "google"),
    ("terraform", R.BUILT_ON, "go"),
    ("nginx", R.BUILT_ON, "c"),
    ("aws", R.USES, "linux"),
    ("webassembly", R.RELATED_TO, "rust"),
    # Languages
    ("cpp", R.EVOLVED_FROM, "c"),
    ("rust", R.RELATED_TO, "cpp"),
    ("go", R.CREATED_BY, "google"),
    ("typescript", R.CREATED_BY, "microsoft"),
    ("swift", R.CREATED_BY, "apple"),
    ("kotlin", R.RELATED_TO, "java"),
    ("python-software-foundation", R.RELATED_TO, "python"),
    # Databases
    ("redis", R.BUILT_ON, "c"),
    ("sqlite", R.BUILT_ON, "c"),
    ("mysql", R.BUILT_ON, "cpp"),
    ("clickhouse", R.BUILT_ON, "cpp"),
    ("mongodb", R.BUILT_ON, "cpp"),
    ("postgresql", R.BUILT_ON, "c"),
    # Python & AI
    ("pandas", R.DEPENDS_ON, "numpy"),
    ("pandas", R.USES, "python"),
    ("scikit-learn", R.DEPENDS_ON, "numpy"),
    ("scikit-learn", R.USES, "python"),
    ("jax", R.USES, "python"),
    ("jax", R.CREATED_BY, "google"),
    ("transformers", R.DEPENDS_ON, "pytorch"),
    ("huggingface", R.BUILDS, "transformers"),
    ("openai", R.BUILDS, "gpt-4"),
    ("anthropic", R.BUILDS, "claude"),
    ("meta", R.BUILDS, "llama"),
    ("meta", R.BUILDS, "pytorch"),
    ("meta", R.BUILDS, "react"),
    ("google", R.BUILDS, "tensorflow"),
    # Web & Frontend
    ("vue", R.BUILT_ON, "javascript"),
    ("svelte", R.BUILT_ON, "javascript"),
    ("angular", R.BUILT_ON, "typescript"),
    ("angular", R.CREATED_BY, "google"),
    ("vite", R.USES, "node-js"),
    ("tailwind-css", R.USES, "javascript"),
    ("django", R.BUILT_ON, "python"),
    ("fastapi", R.BUILT_ON, "python"),
    ("flask", R.BUILT_ON, "python"),
    ("graphql", R.CREATED_BY, "meta"),
    # Tech Giants & Platforms
    ("microsoft", R.OWNS, "github"),
    ("microsoft", R.INVESTS_IN, "openai"),
)

# (slug, year, title) first public appearance, year precision. Seed values, not sourced.
_EVENTS: tuple[tuple[str, int, str], ...] = (
    ("c", 1972, "C language developed at Bell Labs"),
    ("cpp", 1985, "C++ language published"),
    ("python", 1991, "Python first released"),
    ("linux", 1991, "Linux kernel announced"),
    ("mysql", 1995, "MySQL first released"),
    ("java", 1995, "Java announced publicly"),
    ("javascript", 1995, "JavaScript introduced"),
    ("postgresql", 1997, "PostgreSQL 6.0 released"),
    ("sqlite", 2000, "SQLite 1.0 released"),
    ("git", 2005, "Git version control created"),
    ("django", 2005, "Django open-sourced"),
    ("aws", 2006, "Amazon Web Services launched"),
    ("cuda", 2007, "CUDA released"),
    ("github", 2008, "GitHub launched"),
    ("node-js", 2009, "Node.js released"),
    ("go", 2009, "Go language announced"),
    ("redis", 2009, "Redis released"),
    ("mongodb", 2009, "MongoDB 1.0 released"),
    ("flask", 2010, "Flask micro-framework released"),
    ("rust", 2010, "Rust compiler announced"),
    ("typescript", 2012, "TypeScript released"),
    ("docker", 2013, "Docker released"),
    ("react", 2013, "React open-sourced"),
    ("vue", 2014, "Vue.js released"),
    ("swift", 2014, "Swift announced by Apple"),
    ("kubernetes", 2014, "Kubernetes announced"),
    ("tensorflow", 2015, "TensorFlow open-sourced"),
    ("graphql", 2015, "GraphQL open-sourced by Meta"),
    ("next-js", 2016, "Next.js released"),
    ("pytorch", 2016, "PyTorch released"),
    ("webassembly", 2017, "WebAssembly shipped in major browsers"),
    ("fastapi", 2018, "FastAPI released"),
    ("transformers", 2019, "Transformers library released (as pytorch-transformers)"),
    ("jax", 2018, "JAX open-sourced"),
    ("svelte", 2019, "Svelte 3 released"),
    ("tailwind-css", 2019, "Tailwind CSS 1.0 released"),
    ("vite", 2020, "Vite released"),
    ("clickhouse", 2016, "ClickHouse open-sourced"),
    ("llama", 2023, "Llama foundational model released by Meta"),
    ("gpt-4", 2023, "GPT-4 multimodal model released by OpenAI"),
    ("claude", 2023, "Claude model released by Anthropic"),
)


def entity_id(kind: EntityKind, slug: str) -> str:
    """Stable id for a seed entity (a UUIDv5, so database seeding is idempotent)."""
    return str(uuid.uuid5(SEED_NAMESPACE, f"{kind.value}/{slug}"))


def relationship_id(source_id: str, relation: RelationType, target_id: str) -> str:
    """Stable id for a seed relationship row."""
    return str(uuid.uuid5(SEED_NAMESPACE, f"relationship/{source_id}/{relation.value}/{target_id}"))


def seed_entities() -> list[Entity]:
    return [
        Entity(entity_id(kind, slug), kind, slug, name, Provenance.SEED, aliases)
        for slug, kind, name, aliases in _ENTITIES
    ]


def _id_for_slug(slug: str) -> str:
    kinds = [kind for s, kind, _, _ in _ENTITIES if s == slug]
    if len(kinds) != 1:
        raise ValueError(f"seed slug must identify exactly one entity: {slug}")
    return entity_id(kinds[0], slug)


def seed_relationships() -> list[Relationship]:
    return [
        Relationship(_id_for_slug(source), _id_for_slug(target), relation, Provenance.SEED)
        for source, relation, target in _RELATIONSHIPS
    ]


def seed_timeline() -> list[TimelineEvent]:
    return [
        TimelineEvent(
            id=str(uuid.uuid5(SEED_NAMESPACE, f"event/{slug}/{year}")),
            entity_id=_id_for_slug(slug),
            occurred_on=date(year, 1, 1),
            precision=DatePrecision.YEAR,
            title=title,
            event_type="release",
            provenance=Provenance.SEED,
        )
        for slug, year, title in sorted(_EVENTS, key=lambda e: (e[1], e[0]))
    ]


def build_seed_graph() -> InMemoryGraph:
    """Validated seed graph. Raises if the seed breaks any endpoint or uniqueness rule."""
    return InMemoryGraph(seed_entities(), seed_relationships())
