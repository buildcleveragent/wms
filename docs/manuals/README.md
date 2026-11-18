# WMS 角色操作手册索引

**版本：** V2.3

**业务基线：** 2026-11-13 当前工作区代码

本次按以下五类岗位分别编写用户手册。每本均包含登录、安全、岗位入口、操作步骤、完成判断、交接、异常处理和检查表，可独立发放与培训。

“货主端管理员”“货主端业务员”“仓库端操作员”分别对应系统角色“货主管理员”“货主业务员”“仓库操作员”；显示称谓不改变正式权限组。为保持已有下载链接可用，分册文件名沿用原名。

| 角色 | 正式权限组 | 手册 |
|---|---|---|
| 货主端管理员 | `WMS::货主管理员` | [Markdown](金桥融通WMS货主管理员操作手册.md) · [Word](金桥融通WMS货主管理员操作手册.docx) · [PDF](金桥融通WMS货主管理员操作手册.pdf) |
| 货主端业务员 | `WMS::货主业务员` | [Markdown](金桥融通WMS货主业务员操作手册.md) · [Word](金桥融通WMS货主业务员操作手册.docx) · [PDF](金桥融通WMS货主业务员操作手册.pdf) |
| 仓库端操作员 | `WMS::仓库操作员` | [Markdown](金桥融通WMS仓库操作员操作手册.md) · [Word](金桥融通WMS仓库操作员操作手册.docx) · [PDF](金桥融通WMS仓库操作员操作手册.pdf) |
| 仓库管理员 | `WMS::仓库管理员` | [Markdown](金桥融通WMS仓库管理员操作手册.md) · [Word](金桥融通WMS仓库管理员操作手册.docx) · [PDF](金桥融通WMS仓库管理员操作手册.pdf) |
| 系统管理员 | `超级管理员或经过审批的后台管理权限组` | [Markdown](金桥融通WMS系统管理员操作手册.md) · [Word](金桥融通WMS系统管理员操作手册.docx) · [PDF](金桥融通WMS系统管理员操作手册.pdf) |

岗位交接：业务员提交 → 货主端管理员审核 → 仓库管理员确认／发布 → 仓库端操作员作业 → 按流程复核、审核及入账。系统管理员负责账号、配置和审计。申请、审批、作业完成与库存已入账分别确认。

保留的扩展指南（按需发放）：

| 角色 | 正式权限组 | 手册 |
|---|---|---|
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
- [PDA 退货出库使用手册](../pda-return-user-guide.md)
- [Android 移动端使用手册](../android-user-guide.md)
- [商城小程序使用教程](../sales-miniapp-user-guide.md)
- [计费服务技术讲解](../billing_services_guide.md)

维护方式：先编辑 `src/` 源内容，再运行 `.venv/bin/python docs/build_user_manual.py --all`，
最后运行 `.venv/bin/python docs/validate_user_manuals.py`。命令在仓库根目录执行。
构建需要项目 Python 文档依赖、中文字体、LibreOffice 和 Poppler 工具；不连接业务数据库。

角色命名注意：企业岗位“仓库主管”如需管理仓库作业，应使用 `WMS::仓库管理员`。旧用户组“仓库主管”会按兼容逻辑识别为仓库老板。
