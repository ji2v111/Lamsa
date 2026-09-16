from abc import ABC, abstractmethod


class Notifier(ABC):
    """
    أي قناة تنبيه جديدة (واتساب، بريد، SMS...) تورث من هذا الكلاس وتطبّق الدالتين تحت فقط.
    باقي النظام لا يعرف ولا يهتم كيف تشتغل القناة من الداخل.
    """

    name = "base"

    @abstractmethod
    def is_configured(self, tenant) -> bool:
        """هل هذا المحل جاهز يستقبل تنبيه بهذي القناة؟"""
        raise NotImplementedError

    @abstractmethod
    def notify(self, tenant, feedback) -> bool:
        """يرسل التنبيه، ويرجع True لو نجح الإرسال فعليًا."""
        raise NotImplementedError
