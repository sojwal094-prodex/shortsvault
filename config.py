import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # API Keys
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
    AYRSHARE_API_KEY  = os.getenv("AYRSHARE_API_KEY", "")
    AI_PROVIDER       = os.getenv("AI_PROVIDER", "auto")

    # Transcription
    WHISPER_MODE  = os.getenv("WHISPER_MODE", "local")
    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

    # App
    SECRET_KEY           = os.getenv("SECRET_KEY", "dev-secret-change-me")
    MAX_VIDEO_DURATION   = int(os.getenv("MAX_VIDEO_DURATION", 3600))
    OUTPUT_DIR           = os.getenv("OUTPUT_DIR", "./output")
    PORT                 = int(os.getenv("PORT", 5000))
    DEBUG                = os.getenv("DEBUG", "false").lower() == "true"

    # Clip defaults
    MAX_CLIPS            = 7
    MIN_CLIP_DURATION    = 15   # seconds
    MAX_CLIP_DURATION    = 90   # seconds
    TARGET_RESOLUTION    = (1080, 1920)  # 9:16 vertical

    @classmethod
    def has_anthropic(cls):
        return bool(cls.ANTHROPIC_API_KEY and cls.ANTHROPIC_API_KEY != "your_anthropic_key_here")

    @classmethod
    def has_openai(cls):
        return bool(cls.OPENAI_API_KEY and cls.OPENAI_API_KEY != "your_openai_key_here")

    @classmethod
    def has_any_ai(cls):
        return cls.has_anthropic() or cls.has_openai()

    @classmethod
    def effective_ai_provider(cls):
        if cls.AI_PROVIDER == "claude" and cls.has_anthropic():
            return "claude"
        if cls.AI_PROVIDER == "openai" and cls.has_openai():
            return "openai"
        # auto
        if cls.has_anthropic():
            return "claude"
        if cls.has_openai():
            return "openai"
        return "heuristic"
