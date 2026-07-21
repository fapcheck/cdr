import importlib


def dynamic_hook() -> str:
    return "loaded by name"


def load(module_name: str, hook_name: str):
    module = importlib.import_module(module_name)
    return getattr(module, hook_name)
