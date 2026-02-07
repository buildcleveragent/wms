# allapp/core/api/exceptions.py
from rest_framework.views import exception_handler
from rest_framework.response import Response
def custom_exception_handler(exc, ctx):
    resp = exception_handler(exc, ctx)
    if resp is None:
        return Response({"code":"SERVER_ERROR","detail":str(exc)}, status=500)
    resp.data = {"code":"ERROR","detail":resp.data}
    return resp