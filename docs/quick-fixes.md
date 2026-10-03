# SHAF 快速修正指南

适用：已部署的个人调试实例。**本地修复并验证 → 提交推送 → 服务器拉取 → 局部应用 → 复测**。只更新受影响的服务；依赖、数据库迁移及备份见[运维手册](operations.md#5-修改前备份与版本更新)。首次部署见[部署手册](DEPLOY.md)。

## 1. 按文件类型选择方式

| 修改内容 | 应用方式 |
|---|---|
| backend、MCP、classifier 的少量现有 Python 文件 | 复制进对应容器，重启该服务；依赖、启动参数和挂载须保持不变 |
| Vue 页面、样式及前端逻辑 | 必须先编译；推荐构建并发布 frontend 镜像。也可同步完整构建产物，但不能直接复制 `.vue`，需处理资源版本和缓存 |
| `frontend/nginx.conf` | 复制到 frontend 的 `/etc/nginx/conf.d/default.conf`，`nginx -t` 通过后执行 `nginx -s reload` |
| 数据库等服务的原生配置文件 | 确认加载路径、参数及校验方式后，可复制并重启对应服务；当前项目没有自定义 MySQL 配置文件 |
| `data/kb/` 材料、分类模型 | 已挂载，无需复制进容器；知识需补丁重嵌/向量化，模型需停任务和 classifier 后成套更新，见运维第 4 节 |
| `.env`、Compose 环境变量/端口/挂载 | 按实际影响重新创建服务，通常无需构建；restart 不更新容器环境。已有 MySQL 密码/库名不能只改环境变量 |
| MySQL 表、业务数据及 `sql/` | 审核后通过 SQL 执行迁移/修复；首启脚本不会因复制或重启而重放 |
| MySQL、Milvus、etcd、MinIO 的持久数据 | 用数据库/API 或备份恢复流程处理，不直接复制文件覆盖数据目录 |
| 依赖、Dockerfile、文件删除/重命名或大范围改动 | 构建并发布对应镜像；官方基础设施镜像升级需另行核对数据兼容性 |

当前源码写入镜像，未配置源码挂载或自动 reload。共享文件须同步到所有使用它的服务；`data/` 等挂载目录不属于镜像内源码。

## 2. 快速应用 Python 修复

先在本地完成修复、针对性验证、提交和推送。以下均在 **FinalShell/SSH 服务器终端**执行，每一步成功再继续。等待聊天及后台作业结束，停用相关计划任务；重启会短暂中断服务，但保留原挂载中的数据。

```bash
mountpoint -q /mnt/shaf-data || exit 1
cd /mnt/shaf-data/SHAF
dc() { docker compose -f docker-compose.prod.yml "$@"; }
git status --short
git rev-parse --short HEAD
```

工作区必须干净；记下当前提交号，出现未提交改动先核对。拉取已验证的修复：

```bash
git pull --ff-only
```

以修改 `app/main.py` 为例，实际按改动文件替换源/目标路径：

```bash
dc cp ./app/main.py backend:/app/app/main.py
dc exec -T backend sha256sum /app/app/main.py
sha256sum ./app/main.py
```

两处哈希一致后启动新代码：

```bash
dc restart backend
dc exec -T backend curl -fsS http://127.0.0.1:8000/api/health
dc logs --tail 100 backend
```

刚重启时健康接口可能尚未就绪，先看日志，等待启动完成再检查。随后复测原问题及相关页面；health 只证明进程存活。同一容器 restart 通常不需重启 frontend。

多个关联文件一起修改时，先 `dc stop backend`，全部复制后 `dc start backend`，避免混用新旧代码；启动后再核对文件哈希和健康状态。MCP 文件对应 `/app/mcp_servers/`，分类器入口为 `/app/scripts/ch10/serve.py`；换成实际服务名/路径，不照搬 backend 的健康检查命令。

记录修复提交号、复制文件和对应服务。回退时在本地生成回退提交、推送并拉取，再将还原文件复制进容器、重启；**Git 回退不会自动还原容器内文件**。

## 3. 将修复固化到镜像

`cp` 修改保留在当前容器，restart 不丢；删除/重新创建容器后会恢复镜像版本。稳定版本发布前，用服务器已拉取的源码构建新镜像：

```bash
dc build backend
dc up -d --no-deps --force-recreate backend
dc restart frontend
```

第一条只构建镜像，不更换运行中的容器；第二条应用新镜像；第三条让 Nginx 重新解析后端地址。构建失败不替换旧实例。前端发布用 `dc build frontend`，再执行 `dc up -d --no-deps --force-recreate frontend`。classifier 构建/发布加 `--profile classifier`，MCP 使用实际服务名。

构建会执行 Dockerfile 的依赖同步、源码复制和前端编译，按可用缓存复用步骤，无需宿主重装环境。保持原路径/挂载，数据库等无关服务不动；不使用整套 `down` 或删除数据来应用代码修复。

依据：[Docker Compose cp](https://docs.docker.com/reference/cli/docker/compose/cp/)、[容器可写层](https://docs.docker.com/engine/storage/)、[MySQL 首启规则](https://github.com/docker-library/docs/blob/master/mysql/README.md#initializing-a-fresh-instance)、[Nginx reload](https://nginx.org/en/docs/beginners_guide.html#control)。
