from pydantic_ai import Agent
from pydantic_ai.models import infer_model

from .config import settings

# Pydantic AI will automatically pick up other env vars needed. E.g. for Azure set
# AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, OPENAI_API_VERSION
model = infer_model(settings.model)

agent = Agent(model,
    system_prompt="You are a helpful assistant.",
)

@agent.tool_plain
def get_weather(city: str) -> str:
    return f'The weather in {city} is sunny'
