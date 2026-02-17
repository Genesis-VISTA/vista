from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Loading .env manually so they will be added to os.environ and Pydantic AI will pick them up when
# making the model
load_dotenv("../.env")

class Settings(BaseSettings):
    model: str
    """
    Model to run, in format `provier:model`
    
    See https://ai.pydantic.dev/api/providers/ for available providers, and the other env vars
    needed for each.
    """

    host: str = "0.0.0.0"
    port: int = 8000

settings = Settings()
