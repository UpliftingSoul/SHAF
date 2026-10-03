# SHAF 快速修正指南

适用：已部署的个人调试实例。**本地修复并验证 → 提交推送 → 服务器拉取 → 局部应用 → 复测**。只更新受影响的服务；依赖、数据库迁移及备份见[运维手册](operations.md#5-修改前备份与版本更新)。首次部署见[部署手册](DEPLOY.md)。

## 1. 按文件类型选择方式

| 修改内容 | 应用方式 |
|---|---|
| backend、MCP、classifier 的少量现有 Python 文件 | 复制进对应容器，重启该服务；依赖、启动参数和挂载须保持不变 |
| Vue 页面、样式及前端逻辑 | 本地编译，将完整 `dist` 上传并复制到 frontend；无需重建镜像或重启，见第 3 节 |
| `frontend/nginx.conf` | 复制到 frontend 的 `/etc/nginx/conf.d/default.conf`，`nginx -t` 通过后执行 `nginx -s reload` |
| 数据库等服务的原生配置文件 | 确认加载路径、参数及校验方式后，可复制并重启对应服务；当前项目没有自定义 MySQL 配置文件 |
| `data/kb/` 材料、分类模型 | 已挂载，无需复制进容器；知识需补丁重嵌/向量化，模型需停任务和 classifier 后成套更新，见运维第 4 节 |
| `.env`、Compose 环境变量/端口/挂载 | 按实际影响重新创建服务，通常无需构建；restart 不更新容器环境。已有 MySQL 密码/库名不能只改环境变量 |
| MySQL 表、业务数据及 `sql/` | 审核后通过 SQL 执行迁移/修复；首启脚本不会因复制或重启而重放 |
| MySQL、Milvus、etcd、MinIO 的持久数据 | 用数据库/API 或备份恢复流程处理，不直接复制文件覆盖数据目录 |
| Python 依赖、Dockerfile、运行源码删除/重命名或大范围改动 | 构建并发布对应镜像；只改前端 npm 依赖仍可本地安装、编译后同步 `dist`。官方基础设施镜像升级另行核对数据兼容性 |

当前源码写入镜像，未配置源码挂载或自动 reload。共享文件须同步到所有使用它的服务；`data/` 等挂载目录不属于镜像内源码。

## 2. 服务器准备

先在本地完成修复、针对性验证、提交和推送。以下在 **FinalShell/SSH 服务器终端**执行，每一步成功再继续：

```bash
mountpoint -q /mnt/shaf-data || exit 1
cd /mnt/shaf-data/SHAF
dc() { docker compose -f docker-compose.prod.yml "$@"; }
git status --short
```

工作区必须干净，出现未提交改动先核对。拉取已验证的修复并记录版本：

```bash
git pull --ff-only
git log -1 --oneline
```

纯代码/页面修补保留版本和待替换文件即可，无需停整套服务做数据库备份。涉及数据、模型或存储服务变更时，按运维手册备份。

## 3. 快速应用 Vue 修复

**笔记本 PowerShell：** 使用 Node 22，在已提交的同一源码版本上构建。已有本次构建产物时直接复用，不重复编译：

```powershell
cd "D:\Python Project\SHAS\frontend"
npm.cmd run build
```

项目命令已包含类型检查。只有首次安装、依赖缺失或 `package-lock.json` 变化时先执行 `npm.cmd ci`，不必每次重装。

**FinalShell 文件传输：** 将本次完整 `dist` 文件夹上传到服务器 `/mnt/shaf-data/SHAF/backups/frontend-preview/提交号/dist`，提交号使用上一步显示的短提交号，每次使用新批次目录。等待全部上传成功；`dist` 不入 Git，不能靠 `git pull` 获取。

**服务器终端：** 本项目当前 `dist` 为 `index.html` 和 `assets/`，先复制全部资源，再更新页面入口：

```bash
release="backups/frontend-preview/$(git rev-parse --short HEAD)/dist"
dc cp "$release/assets/." frontend:/usr/share/nginx/html/assets/ &&
dc cp "$release/index.html" frontend:/usr/share/nginx/html/index.html
```

无需 restart 或 Nginx reload，浏览器刷新并复测修改页面即可；仍显示旧内容时 Ctrl+F5。前端已有入口不缓存、哈希资源长期缓存的配置；保留旧 assets，避免已打开页面加载旧分块失败，不清空在线静态目录。只改静态页面不必停止后端作业。

回退时将 `release` 改为旧批次的 `dist` 路径，再按上述顺序复制。若同时修改 Nginx 配置，则按第 1 节另行校验、reload。

## 4. 快速应用 Python 修复

等待聊天及后台作业结束，停用相关计划任务。重启会短暂中断该服务，但保留原挂载中的数据。

以修改 `app/main.py` 为例，实际按改动文件替换源/目标路径：

```bash
dc cp ./app/main.py backend:/app/app/main.py
dc restart backend
dc exec -T backend curl -fsS http://127.0.0.1:8000/api/health
```

刚重启时健康接口可能尚未就绪，等待启动完成再检查；报错时用 1Panel 日志或 `dc logs --tail 100 backend`。随后复测原问题及相关页面；health 只证明进程存活。同一容器 restart 通常不需重启 frontend。

多个关联文件一起修改时，先 `dc stop backend`，全部复制后 `dc start backend`，避免混用新旧代码；启动后复测。MCP 文件对应 `/app/mcp_servers/`，分类器入口为 `/app/scripts/ch10/serve.py`；换成实际服务名/路径，不照搬 backend 的健康检查命令。

记录修复提交号、复制文件和对应服务。回退时在本地生成回退提交、推送并拉取，再将还原文件复制进容器、重启；**Git 回退不会自动还原容器内文件**。

## 5. 将修复固化到镜像

Python 文件和 Vue 静态产物的 `cp` 修改都保留在当前容器，restart 不丢；删除/重新创建容器后会恢复镜像版本。正式发布或需要可重建版本时，用服务器已拉取的源码构建新镜像；日常小修不重复此步骤：

```bash
dc build backend
dc up -d --no-deps --force-recreate backend
dc restart frontend
```

第一条只构建镜像，不更换运行中的容器；第二条应用新镜像；第三条让 Nginx 重新解析后端地址。构建失败不替换旧实例。前端发布用 `dc build frontend`，再执行 `dc up -d --no-deps --force-recreate frontend`。classifier 构建/发布加 `--profile classifier`，MCP 使用实际服务名。

构建会执行 Dockerfile 的依赖同步、源码复制和前端编译，按可用缓存复用步骤，无需宿主重装环境。保持原路径/挂载，数据库等无关服务不动；不使用整套 `down` 或删除数据来应用代码修复。

依据：[Vite 静态部署](https://vite.dev/guide/static-deploy.html#building-the-app)、[Docker Compose cp](https://docs.docker.com/reference/cli/docker/compose/cp/)、[容器可写层](https://docs.docker.com/engine/storage/)、[MySQL 首启规则](https://github.com/docker-library/docs/blob/master/mysql/README.md#initializing-a-fresh-instance)、[Nginx reload](https://nginx.org/en/docs/beginners_guide.html#control)。
