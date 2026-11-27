"""Conservative, model-free classification of standalone acknowledgements."""
import unicodedata


_ACKNOWLEDGEMENTS = frozenset({"谢谢", "謝謝", "谢谢大家", "謝謝大家", "感谢", "感謝", "阿们", "阿們", "amen", "thanks", "thank you"})
# Only terminal decoration is stripped. Never delete punctuation inside prose.
_TRAILING = " \t\r\n!！。．.🙏\ufe0f🏻🏼🏽🏾🏿"


def standalone_acknowledgement(msg):
    if (msg.origin != "whatsapp" or msg.quoted_message_id or msg.source_sent_at
            or msg.revoked or msg.unverified_edit or msg.unsupported_media):
        return False
    text = unicodedata.normalize("NFKC", msg.text).strip().rstrip(_TRAILING).strip().casefold()
    return text in _ACKNOWLEDGEMENTS
