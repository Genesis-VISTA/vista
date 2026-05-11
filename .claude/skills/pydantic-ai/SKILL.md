---
name: pydantic-ai
description: >-
  Best practices and patterns for building agents and agentic applications with
  PydanticAI. Use when writing or reviewing PydanticAI agents, tools, dependencies,
  structured output, streaming, or multi-turn conversations.
metadata:
  version: "latest"
  tags: ["PydanticAI", "LLM", "Agents", "Python", "AI"]
---

# PydanticAI — Best Practices

PydanticAI is a Python framework for building production-grade LLM applications. It brings FastAPI-style ergonomics to agent development, with type-safe dependencies, tools, and structured output via Pydantic.

## Agent Basics

```python
from pydantic_ai import Agent

agent = Agent(
    'anthropic:claude-sonnet-4-6',  # model string: 'provider:model-id'
    instructions='Be concise and accurate.',
)

result = agent.run_sync('What is 2 + 2?')
print(result.output)   # '4'
```

### Model strings

```python
'openai:gpt-4o'
'anthropic:claude-opus-4-7'
'anthropic:claude-sonnet-4-6'
'google-gla:gemini-2.0-flash'
'groq:llama-3.1-70b-versatile'
```

## Structured Output

Define a Pydantic model as `output_type`; the agent validates the response and retries on failure:

```python
from pydantic import BaseModel
from pydantic_ai import Agent

class Analysis(BaseModel):
    summary: str
    confidence: float
    key_findings: list[str]

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    output_type=Analysis,
    instructions='Analyze the provided text.',
)

result = agent.run_sync('Analyze the following paper: ...')
print(result.output.confidence)   # typed, validated
```

## Dependencies (Dependency Injection)

Use `deps_type` to inject external resources (DB connections, API clients, config) into tools and instructions. Define dependencies as a dataclass:

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

@dataclass
class AppDeps:
    db: DatabaseConnection
    api_key: str
    user_id: str

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    deps_type=AppDeps,
)

@agent.tool
async def get_user_data(ctx: RunContext[AppDeps]) -> dict:
    """Fetch the current user's profile."""
    return await ctx.deps.db.users.find(ctx.deps.user_id)

# Inject deps at run time
deps = AppDeps(db=db_conn, api_key=os.environ['API_KEY'], user_id='u123')
result = await agent.run('What is my profile?', deps=deps)
```

## Tools

### `@agent.tool` — with RunContext (access to deps, retry count, etc.)

```python
@agent.tool
async def search_papers(ctx: RunContext[AppDeps], query: str, limit: int = 5) -> list[dict]:
    """Search research papers by keyword. Returns title, abstract, and DOI."""
    return await ctx.deps.db.papers.search(query, limit=limit)
```

- The docstring becomes the tool description sent to the model
- Type hints on parameters become the tool's JSON schema
- `ctx: RunContext[DepsType]` must be the first parameter

### `@agent.tool_plain` — no context needed

```python
from pydantic_ai import ModelRetry

@agent.tool_plain(retries=3)
def calculate_density(mass_kg: float, volume_m3: float) -> float:
    """Calculate density in kg/m³."""
    if volume_m3 <= 0:
        raise ModelRetry('Volume must be positive — please provide a valid volume')
    return mass_kg / volume_m3
```

- Use `ModelRetry` to send an error message back to the model for self-correction
- `retries` parameter controls per-tool retry count

### Async tools

```python
@agent.tool
async def fetch_data(ctx: RunContext[AppDeps], url: str) -> str:
    """Fetch content from an external URL."""
    async with ctx.deps.http_client.get(url) as resp:
        return await resp.text()
```

## Instructions & System Prompts

**Static** (set at agent construction, not preserved across conversation turns):
```python
agent = Agent('anthropic:claude-sonnet-4-6', instructions='Always respond in JSON.')
```

**Dynamic** (computed at run time, can use deps):
```python
@agent.instructions
def personalized_instructions(ctx: RunContext[AppDeps]) -> str:
    return f'You are assisting user {ctx.deps.user_id}. Be friendly.'
```

**System prompts** (preserved in message history across turns — use for context that should persist):
```python
@agent.system_prompt
def add_date_context() -> str:
    from datetime import date
    return f'Today is {date.today().isoformat()}.'
```

**Distinction**: instructions are not included in the persisted message history; system prompts are.

## Run Methods

| Method | Use case |
|---|---|
| `agent.run_sync(prompt, deps=..., message_history=...)` | Simple synchronous calls |
| `await agent.run(prompt, deps=..., message_history=...)` | Async calls |
| `async with agent.run_stream(prompt, ...) as resp:` | Stream text output token by token |
| `async for event in agent.run_stream_events(prompt, ...):` | Raw event stream |

```python
# Async
result = await agent.run('Summarize this document', deps=deps)
print(result.output)
print(result.usage())  # token counts

# Streaming
async with agent.run_stream('Tell me a story', deps=deps) as resp:
    async for chunk in resp.stream_text():
        print(chunk, end='', flush=True)
```

## Multi-Turn Conversations

Pass `message_history` to continue a conversation:

```python
result1 = agent.run_sync('What is the melting point of NaCl?', deps=deps)

result2 = agent.run_sync(
    'What about KCl?',
    deps=deps,
    message_history=result1.new_messages(),  # preserves context
)
```

Use `result.all_messages()` to get the full history including the current exchange, or `result.new_messages()` for only the new messages from this run.

## RunContext Reference

```python
ctx.deps          # injected dependencies (type: DepsType)
ctx.retry         # current retry attempt (0-indexed)
ctx.usage         # token/request statistics so far
ctx.model_settings  # active model configuration
ctx.metadata      # run tags / additional context dict
```

## Error Handling

```python
from pydantic_ai import ModelRetry, UnexpectedModelBehavior

@agent.tool_plain
def parse_temperature(value: str) -> float:
    """Parse a temperature string like '300 K' or '27 C'."""
    try:
        # ... parsing logic ...
        return result
    except ValueError:
        raise ModelRetry(f'Could not parse temperature from "{value}". Please provide a numeric value with unit (K or C).')
```

`ModelRetry` triggers a retry with the error message sent back to the model. Pydantic validation errors on `output_type` are handled the same way automatically.

## Agent Composition & Capabilities

Group tools, instructions, and model settings into reusable **capabilities** rather than duplicating across agents:

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import Thinking  # built-in

# Extended thinking for complex reasoning
agent = Agent(
    'anthropic:claude-opus-4-7',
    capabilities=[Thinking(budget_tokens=5000)],
)
```

For tool reuse across agents, define tools as plain functions and register them on multiple agents:

```python
async def lookup_salt(ctx: RunContext[AppDeps], formula: str) -> dict:
    """Look up salt properties from the MSTDB."""
    return await ctx.deps.db.get_salt(formula)

agent_a = Agent('anthropic:claude-sonnet-4-6', deps_type=AppDeps)
agent_b = Agent('openai:gpt-4o', deps_type=AppDeps)
agent_a.tool(lookup_salt)
agent_b.tool(lookup_salt)
```

## Type-Safety Patterns

Always annotate `Agent` with its type parameters for IDE support:

```python
from pydantic_ai import Agent

agent: Agent[AppDeps, Analysis] = Agent(
    'anthropic:claude-sonnet-4-6',
    deps_type=AppDeps,
    output_type=Analysis,
)
```

## Testing

PydanticAI provides a `TestModel` and `FunctionModel` for unit testing without hitting real LLM APIs:

```python
from pydantic_ai.models.test import TestModel

with agent.override(model=TestModel()):
    result = agent.run_sync('test prompt', deps=test_deps)
    # TestModel returns predictable structured output for testing
```

## Common Anti-Patterns to Avoid

- **Don't** put secrets or external state in the agent definition — inject via `deps`
- **Don't** use `run_sync` inside an async context — use `await agent.run()`
- **Don't** raise plain exceptions in tools when the model should retry — use `ModelRetry`
- **Don't** await multiple independent agent calls sequentially — run them with `asyncio.gather`
- **Don't** hardcode model strings in tool logic — keep model selection at the agent level
- **Don't** use `system_prompt` for one-off run context — use `instructions` instead
- **Don't** ignore `result.usage()` in production — monitor token counts for cost control
