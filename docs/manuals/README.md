# WMS 角色操作手册索引

**版本：** V2.1

**业务基线：** 2026-09-07 当前工作区代码

请按实际岗位领取对应手册。每本分册均包含该角色所需的登录、安全、状态、操作、异常处理和检查表，可独立使用。

| 角色 | 正式权限组 | 手册 |
|---|---|---|
| 仓库操作员 | `WMS::仓库操作员` | [Markdown](金桥融通WMS仓库操作员操作手册.md) · [Word](金桥融通WMS仓库操作员操作手册.docx) · [PDF](金桥融通WMS仓库操作员操作手册.pdf) |
| 仓库管理员 | `WMS::仓库管理员` | [Markdown](金桥融通WMS仓库管理员操作手册.md) · [Word](金桥融通WMS仓库管理员操作手册.docx) · [PDF](金桥融通WMS仓库管理员操作手册.pdf) |
| 货主业务员 | `WMS::货主业务员` | [Markdown](金桥融通WMS货主业务员操作手册.md) · [Word](金桥融通WMS货主业务员操作手册.docx) · [PDF](金桥融通WMS货主业务员操作手册.pdf) |
| 货主管理员 | `WMS::货主管理员` | [Markdown](金桥融通WMS货主管理员操作手册.md) · [Word](金桥融通WMS货主管理员操作手册.docx) · [PDF](金桥融通WMS货主管理员操作手册.pdf) |
| 系统管理员 | `超级管理员或经过审批的后台管理权限组` | [Markdown](金桥融通WMS系统管理员操作手册.md) · [Word](金桥融通WMS系统管理员操作手册.docx) · [PDF](金桥融通WMS系统管理员操作手册.pdf) |
| 仓库老板 | `WMS::仓库老板` | [Markdown](金桥融通WMS仓库老板经营看板手册.md) · [Word](金桥融通WMS仓库老板经营看板手册.docx) · [PDF](金桥融通WMS仓库老板经营看板手册.pdf) |

完整合订版位于上级目录：

- [Markdown](../金桥融通WMS操作手册.md)
- [Word](../金桥融通WMS操作手册.docx)
- [PDF](../金桥融通WMS操作手册.pdf)

维护与核对资料：

- [角色—权限—动作矩阵](ROLE_MATRIX.md)
- [本次业务基线](BASELINE.md)
- [内容核对记录](CONTENT_REVIEW.md)
- [文档验收记录](VALIDATION.md)

专项指南：

- [POS 使用手册](../pos_user_manual.md)
- [商城小程序使用教程](../sales-miniapp-user-guide.md)
- [计费服务技术讲解](../billing_services_guide.md)

维护方式：先编辑 `src/` 源内容，再运行 `.venv/bin/python docs/build_user_manual.py --all`，
最后运行 `.venv/bin/python docs/validate_user_manuals.py`。命令在仓库根目录执行。
构建需要项目 Python 文档依赖、中文字体、LibreOffice 和 Poppler 工具；不连接业务数据库。

角色命名注意：企业岗位“仓库主管”如需管理仓库作业，应使用 `WMS::仓库管理员`。旧用户组“仓库主管”会按兼容逻辑识别为仓库老板。
