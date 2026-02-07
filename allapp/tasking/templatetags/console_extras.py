from django import template
register = template.Library()

@register.filter
def startswith(value, arg):
    """Return True if string 'value' starts with 'arg'. Safe for None."""
    try:
        return str(value).startswith(str(arg))
    except Exception:
        return False
