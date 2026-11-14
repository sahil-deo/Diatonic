from .app import NotesApp


def create(ctx):
    return NotesApp(ctx)
