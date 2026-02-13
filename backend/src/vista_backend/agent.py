from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.azure import AzureProvider

from .config import settings

model = OpenAIChatModel(
    settings.azure_openai_deployment,
    provider=AzureProvider(
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
        api_key=settings.azure_openai_api_key,
    ),
)

agent = Agent(
    model=model,
    system_prompt="You are a helpful assistant.",
)

@agent.tool_plain
def get_weather(city: str) -> str:
    return f'The weather in {city} is sunny'

def get_agent() -> Agent:
    return agent
