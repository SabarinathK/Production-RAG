import re
import logging
from app.config import settings

logger = logging.getLogger(__name__)

# Fallback regex patterns for PII scrubbing when Presidio NLP model is loading/unavailable
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
PHONE_REGEX = re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')
SSN_REGEX = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
CREDIT_CARD_REGEX = re.compile(r'\b(?:\d[ -]*?){13,16}\b')

class PIIRedactor:
    def __init__(self):
        self.analyzer = None
        self.anonymizer = None
        self._init_presidio()

    def _init_presidio(self):
        if not settings.ENABLE_PII_REDACTION:
            return
        try:
            import spacy
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            try:
                spacy.load("en_core_web_sm")
            except OSError:
                logger.info("Downloading lightweight spaCy model 'en_core_web_sm' (~12MB)...")
                from spacy.cli import download
                download("en_core_web_sm")

            configuration = {
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]
            }
            provider = NlpEngineProvider(nlp_configuration=configuration)
            nlp_engine = provider.create_engine()
            self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
            self.anonymizer = AnonymizerEngine()
            logger.info("Presidio PII Redaction Engine loaded successfully with en_core_web_sm.")
        except Exception as e:
            logger.info(f"Presidio NLP initialization note (using instant regex PII scrubbing fallback): {e}")


    def redact(self, text: str) -> str:
        if not settings.ENABLE_PII_REDACTION or not text:
            return text

        active_entities = ["CREDIT_CARD", "US_SSN"]
        if getattr(settings, "PII_REDACT_EMAIL", False):
            active_entities.append("EMAIL_ADDRESS")
        if getattr(settings, "PII_REDACT_PHONE", False):
            active_entities.append("PHONE_NUMBER")
        if getattr(settings, "PII_REDACT_PERSON", False):
            active_entities.append("PERSON")

        if self.analyzer and self.anonymizer:
            try:
                results = self.analyzer.analyze(text=text, entities=active_entities, language="en")
                anonymized_result = self.anonymizer.anonymize(text=text, analyzer_results=results)
                return anonymized_result.text
            except Exception as e:
                logger.warning(f"Presidio analyze error, fallback to regex scrubbing: {e}")

        # Fallback regex masking
        redacted = text
        if getattr(settings, "PII_REDACT_EMAIL", False):
            redacted = EMAIL_REGEX.sub("[EMAIL REDACTED]", redacted)
        if getattr(settings, "PII_REDACT_PHONE", False):
            redacted = PHONE_REGEX.sub("[PHONE REDACTED]", redacted)
        redacted = SSN_REGEX.sub("[SSN REDACTED]", redacted)
        redacted = CREDIT_CARD_REGEX.sub("[CREDIT CARD REDACTED]", redacted)
        return redacted

pii_redactor = PIIRedactor()
