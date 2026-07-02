"""Small arithmetic helpers used by the eval corpus's fixture tasks."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a + b  # bug: should multiply, not add
