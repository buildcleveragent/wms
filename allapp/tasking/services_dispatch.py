## 9) 与“发运落账”服务对接：完成后定稿快照 **文件：`allapp/tasking/services_dispatch.py`（收尾处增加）**
# ... 在 post_dispatch_task() 成功提交后：
from allapp.reports.services import snapshot_dispatch_note

# 任务头回填保存后：
commit_posting_batch(posting_batch)

# —— 定稿快照（幂等）：
snapshot_dispatch_note(task, by_user=by_user, save_html=True, finalize=True)
    return created, posting_batch