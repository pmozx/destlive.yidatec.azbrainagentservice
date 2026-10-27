# 智能体应用监控台（d00-fastapi-integration）

基于 **FastAPI + Vue 3** 的前后端一体化示例工程：后端提供 Agent 运行时相关接口（用户、任务、接口自检等），前端为与之同源部署的监控台，构建产物由 FastAPI 的 `/ui` 直接托管。

- 后端：FastAPI 工厂模式 + 异步 SQLAlchemy 2.0 + APScheduler + loguru，路由自动扫描注册，同时提供 Swagger / ReDoc / YaoAPI 三套接口文档
- 前端：Vue 3 + Vite 7 + Element Plus，顶部横向导航 + 白色大圆角卡片布局，酒红紫主题，支持深色模式与主题色切换
- 工程化：uv 管理 Python 依赖、npm 管理前端依赖；pytest + ruff；SCSS（BEM + mixins）

## 技术栈

| 层次 | 选型 |
| --- | --- |
| Web 框架 | FastAPI、uvicorn[standard]、YaoAPI |
| 数据层 | SQLAlchemy 2.0（async）、asyncpg（PostgreSQL 主选）/ aiomysql / aiosqlite、alembic |
| 向量存储 | PostgreSQL + pgvector（`pgvector` Python 包 + HNSW 余弦索引，表 `kb_chunk`） |
| 配置与日志 | pydantic-settings、python-dotenv、loguru |
| 调度 | APScheduler 3.x |
| 前端 | Vue 3.5、Vite 7、Vue Router 4、Pinia 3、Element Plus 2.x、SCSS(sass) |
| 质量 | pytest、pytest-asyncio、ruff |

## 目录结构

```text
d00-fastapi-integration/
├── main.py                  # 启动入口（默认 0.0.0.0:8002，热重载）
├── app/
│   ├── __init__.py          # create_app：路由注册、YaoAPI、前端托管
│   ├── api/                 # API 层：自动扫描注册 APIRouter
│   │   ├── api00_start/ ~ api03_depends/   # 示例：基础/中间件/依赖注入
│   │   ├── module01_base/item/             # item 模块 CRUD
│   │   └── v1/module_system/user/          # 用户模块 CRUD
│   ├── core/                # 数据库、向量存储（pgvector）、依赖、异常、日志、中间件、校验器
│   ├── config/              # 路径常量 + Settings（多环境）
│   ├── common/              # 常量、枚举、响应体、请求体
│   ├── utils/               # banner、密码、通用工具
│   └── alembic/             # 数据库迁移
├── env/                     # 多环境配置：.env / .env.dev / .env.test / .env.prod ...
├── static/                  # 前端构建产物（/ui 托管）
├── tests/                   # pytest 用例
├── logs/  data/             # 运行日志、SQLite 数据文件
└── frontend/                # Vue 3 前端工程
    ├── src/
    │   ├── api/             # 接口封装（module_system / module_monitor）
    │   ├── layouts/         # 顶栏、页签、设置抽屉、布局骨架
    │   ├── views/           # dashboard、module_system、module_monitor、error
    │   ├── components/      # SectionCard、StatCard、JsonViewer、TaskStatusTag
    │   ├── assets/styles/   # SCSS：index / reset / variables / mixins
    │   ├── router/ store/ config/ hooks/ utils/ enums/
    │   └── main.js
    ├── vite.config.js       # base=/ui/，构建输出到 ../static
    └── scripts/clean.mjs    # 构建前精确清理 static
```

## 快速开始

### 后端

```bash
# 安装依赖（含 dev 组）
uv sync

# 启动（仓库内已带 .venv 时也可直接运行）
python main.py
# 等价写法
uvicorn app:create_app --factory --host 0.0.0.0 --port 8002 --reload
```

| 入口 | 地址 |
| --- | --- |
| 前端页面 | http://127.0.0.1:8002/ui |
| Swagger | http://127.0.0.1:8002/docs |
| ReDoc | http://127.0.0.1:8002/redoc |
| OpenAPI JSON | http://127.0.0.1:8002/openapi.json |
| YaoAPI 聚合文档 | http://127.0.0.1:8002/docs/yao-api |

### 前端

```bash
cd frontend
npm install
npm run dev     # 开发服务器 5173，访问 http://127.0.0.1:5173/ui/
npm run build   # 产物写入 ../static，随后由 FastAPI /ui 托管
```

开发环境通过 Vite 代理把 `/api00_start`、`/item`、`/system`、`/docs` 等前缀转发到 `http://127.0.0.1:8002`，因此前后端都使用同源相对路径，生产环境无需改配置。

## 配置说明

### 后端多环境

`app/config/setting.py` 按 Spring Boot profiles 的方式选择配置：

1. 优先读取系统环境变量 `ENVIRONMENT`
2. 其次读取 `env/.env` 中的 `ENVIRONMENT`
3. 都未设置时使用 `dev`，加载 `env/.env.dev`

数据库类型由 `DATABASE_TYPE` 决定（`postgres` / `mysql` / `sqlite`），异步驱动与连接串自动拼接；连接池、会话、日志级别均可在 `env/.env.{environment}` 中覆盖。

本工程默认使用 **PostgreSQL**，并通过 **pgvector** 承担向量存储：

```ini
DATABASE_TYPE=postgres
DATABASE_HOST=localhost          # 容器部署时改成 service-brain-postgres
DATABASE_PORT=5432
VECTOR_ENABLE=true
VECTOR_DIM=1024                  # 须与 embedding 模型输出一致（bge-m3=1024）
VECTOR_AUTO_CREATE=true          # 启动时幂等建扩展、切块表与 HNSW 索引
```

向量接口：`GET /system/vector/status`、`POST /system/vector/chunks/upsert`、`POST /system/vector/chunks/search`、
`DELETE /system/vector/chunks`（embedding 由调用方生成，接口只做维度校验与存取）。

### 前端环境

| 文件 | 用途 |
| --- | --- |
| `.env.development` | 标题、`VITE_APP_BASE=/ui/`、代理目标与代理前缀 |
| `.env.production` | 标题、base，接口走同源相对路径 |

## 前端功能

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `/dashboard` | 运行总览 | Hero 检索、指标条、最近任务、快捷入口、后端服务信息 |
| `/system/user` | 用户管理 | 对接 `/system/users`，分页查询、新增、编辑、详情、删除 |
| `/monitor/agent` | 任务监控 | 任务列表筛选、分页、进度展示、详情抽屉（当前为示例数据） |
| `/monitor/endpoint` | 接口自检 | 接口清单 + 逐个探测，JSON 展示响应与耗时 |

布局与交互：

- 固定顶栏（深紫 → 品红渐变）：品牌区、横向菜单、明暗切换、刷新、全屏、界面设置、用户下拉
- 菜单选中统一为白色胶囊；进入子路由时父级菜单通过 `is-current` 保持与一级菜单一致的选中样式
- 面包屑 + 页签胶囊（页签数量 > 1 时显示），页面切换带过渡动画
- 界面设置抽屉：深浅模式、主题色预设（酒红紫/品红/黛紫/拂晓蓝/极光绿）、页签与面包屑开关、恢复默认

## 样式体系（SCSS）

`src/assets/styles/`：

- `index.scss`：全局入口，`@use` 组合其余文件；页面骨架、工具类、Element Plus 覆盖（按钮渐变、输入框描边、表格浅色表头、分页、弹出菜单等）
- `variables.scss`：设计令牌（导航渐变、圆角、字号色阶、阴影、深色主题）
- `reset.scss`：轻量重置、滚动条、`scrollbar-gutter: stable`
- `mixins.scss`：`flex` / `flex-center` / `ellipsis` / `ellipsis-lines` / `panel` / `nav-pill`

约定：

- 组件统一 `<style scoped lang="scss">`，使用 BEM + `&__` 嵌套收敛重复选择器
- `vite.config.js` 通过 `css.preprocessorOptions.scss.additionalData` 全局注入 mixins，组件内可直接 `@include`
- 纵向 flex 默认 `align-items: stretch`，避免容器按内容收缩
- 宽度基线：`.app-container` 最大 1560px、左右 32px；`.page` 及其直接子块、卡片均为 100% 宽，保证各路由页面与页内区块同宽

## 后端要点

- 路由注册：`register_routers()` 先注册 `module_system`，再自动扫描 `app.api` 包下所有 `APIRouter` 实例
- 中间件：`register_middlewares()` 已实现（装饰器 + 类中间件），当前在 `create_app()` 中默认注释，需要时取消
- 前端托管：`app.frontend("/ui", directory=STATIC_DIR, fallback="index.html")`，支持前端 history 路由直达
- 日志：loguru 输出到控制台与 `logs/`，支持按大小滚动与 gzip 归档

## 测试与质量

```bash
uv run pytest              # 单元测试
uv run ruff check .        # 静态检查
uv run ruff format .       # 格式化
cd frontend && npm run build   # 前端构建校验
```

## 部署（Docker Compose）

本仓库已内置 CI/CD 与 devops（单镜像单容器，前端随镜像构建并由 FastAPI 在 `/ui` 同源托管）：

```bash
# 一键：构建门禁 → 构建并推送镜像 → docker compose 发布
devops/scripts/00-deploy-all.sh --env=dev

# 部署机手动发布 / 回滚
devops/scripts/manual/deploy.sh prod 20261026103000
devops/scripts/manual/rollback.sh prod 20261025213000
```

- 应用运行配置唯一来源：`env/.env.<env>`（部署时挂载进容器 `/home/env/`，由 `app/config/setting.py` 加载）
- 数据库：PostgreSQL 17 + pgvector；自建中间件用 `devops/common-server/docker-compose.yml`（`pgvector/pgvector:pg17`，首次初始化自动 `CREATE EXTENSION vector`）
- 环境矩阵：`dev` / `test` / `uat` / `prod`；容器内固定监听 `8002`，宿主机端口由 `APP_PORT` 控制
- 流水线双入口：`.gitlab-ci.yml`（单 Job，默认关闭待启用）与 `Jenkinsfile`（手动触发）
- 详细说明（目录结构、配置与口令契约、变量清单、落地步骤）：[devops/README.md](./devops/README.md)

## 约定与注意事项

- `static/` 是前端构建产物目录；`npm run build` 只清理 `assets/`、`index.html`、`favicon.svg`，保留 `static/images/` 等后端演示资源
- `env/` 是配置目录（不是虚拟环境），不要加入 `.gitignore`
- 默认主题色在 `index.html` 首屏脚本中预置，避免闪蓝；若浏览器 localStorage 存有旧主题色，可在「界面设置 → 恢复默认设置」后生效
- 服务端口以 `main.py` 为准（8002）；`env` 中的 `HOST`/`PORT` 便于外部部署时对齐
- 仓库根目录 `00-document/`、`01-document-AI Agent/` 为配套学习笔记，与运行无关
