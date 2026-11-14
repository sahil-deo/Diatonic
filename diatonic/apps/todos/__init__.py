from .app import TodosApp


def create(ctx):
    return TodosApp(ctx)
