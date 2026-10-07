import uvicorn
from radio_listener.config import settings
from radio_listener.api.server import app

def main():
    uvicorn.run(
        "radio_listener.api.server:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False
    )

if __name__ == "__main__":
    main()
