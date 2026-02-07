from django import template
register = template.Library()

@register.filter(name="add_class")
def add_class(bound_field, css):
    """
    用法：{{ field|add_class:"input" }}
    在不改 Form 的前提下给控件附加 CSS class。
    """
    # 非表单字段直接原样返回
    if not hasattr(bound_field, "as_widget"):
        return bound_field
    widget = bound_field.field.widget
    existing = (widget.attrs.get("class") or "").strip()
    classes = f"{existing} {css}".strip() if existing else css
    attrs = dict(widget.attrs, **{"class": classes})
    return bound_field.as_widget(attrs=attrs)

@register.filter(name="attr")
def attr(bound_field, args):
    """
    用法：{{ field|attr:"placeholder=请扫描条码" }}
    动态设置任意属性。
    """
    if not hasattr(bound_field, "as_widget"):
        return bound_field
    key, _, val = (args or "").partition("=")
    key = key.strip()
    val = val.strip()
    if not key:
        return bound_field
    attrs = dict(bound_field.field.widget.attrs, **{key: val})
    return bound_field.as_widget(attrs=attrs)
