import typer
import uvicorn
from decouple import config
from ws_server.api.app import app as fastapi_app # Renamed to avoid conflict

from .replacements import replacements_app # Import the replacements app
from .prompts import prompts_app # Import the prompts app

app = typer.Typer(help="Manage database entries for replacements, initial prompts, and run the server.")

# Add the replacements commands
app.add_typer(replacements_app, name="replacements", help="Manage replacement rules.")
app.add_typer(prompts_app, name="prompts", help="Manage the initial prompt.")

@app.command(help="Run the Uvicorn server.")
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind the server to."),
    port: int = typer.Option(7000, help="Port to bind the server to."),
    log_level: str = typer.Option(config("LOGLEVEL", default="info"), help="Logging level for Uvicorn.")
):
    """
    Starts the Uvicorn server with the FastAPI application.
    """
    uvicorn.run(fastapi_app, host=host, port=port, log_level=log_level)

if __name__ == "__main__":
    app()