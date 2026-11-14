from .app import MemoryApp


def create(ctx):
    return MemoryApp(ctx)
