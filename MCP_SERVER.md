# Key Features of joern_mcp.py

## Neo4j Connectivity

- Connects to a Neo4j database containing the Joern Code Property Graph (CPG)
- Uses environment variables for configuration (`NEO4J_URI`, `NEO4J_USER`, etc.)
- Implements async sessions for efficient database queries

## AI Integration

- Uses the `Agent` class from `pydantic-ai` to create a Joern expert
- Defines a `JoernAgentDeps` class for dependencies (Neo4j, Anthropic)
- Implements a system prompt specializing in code security analysis
- Leverages Claude-3 Sonnet for advanced language understanding

## RAG Capabilities

- Implements embedding-based similarity search for code
- Retrieves relevant code snippets based on semantic similarity
- Uses Claude embeddings for query-code matching
- Optimized for 1024-dimensional embedding space

## FastMCP Tools

| Tool | Description |
|------|-------------|
| `run_query` | Execute arbitrary Cypher queries |
| `get_node_labels` | Retrieve all node types in the graph |
| `get_relationship_types` | List relationship types in the graph |
| `find_code_functions` | Find functions matching a pattern |
| `find_security_vulnerabilities` | Identify potential security issues |
| `analyze_code_function` | Deep analysis of specific functions |
| `trace_code_dataflow` | Follow data through the code |
| `search_for_code_patterns` | Find specific code patterns |
| `rag_code_query` | Semantic search through the code |

## How It Works

1. The server initializes with connections to Neo4j and Anthropic Claude
2. It creates various tools to query and analyze the code graph
3. The RAG functionality uses Claude embeddings to find semantically similar code
4. All queries are executed against the Neo4j database containing the Joern-generated graph
5. Results are formatted and returned in a user-friendly format

## Setup and Usage

### Prerequisites
- A running Neo4j instance with Joern CPG data imported
- Anthropic API access for embeddings and LLM capabilities
- Python 3.13 or higher

### Environment Variables
```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
ANTHROPIC_API_KEY=your_api_key
```

### Installation
```bash
# Using uv (recommended)
uv pip install -e .

# With development dependencies
uv pip install -e ".[dev]"
```

### Running the Server
```bash
python joern_mcp.py
```

## Implementation Details

This implementation combines graph database analysis with modern AI capabilities:
- Uses async operations for better performance
- Integrates Claude-3 Sonnet for advanced language understanding
- Provides comprehensive code security analysis tools
- Follows best practices for RAG implementations
- Maintains clean separation of concerns between graph operations and AI functionality
