// ==============================================================================
// 本仓库 Jenkins 流水线（service_brain_agent）—— 单 stage，仅手动构建
//
//   本文件不配置任何环境变量（含口令），唯一职责是把目标环境名传给一键脚本：
//     bash devops/scripts/00-deploy-all.sh --env=<ENV_NAME>
//
//   配置唯一来源为应用自身的 env/.env.<env>（部署时挂载进容器，由
//   pydantic-settings 加载），流水线不复制、不渲染任何配置；镜像仓库口令来自
//   执行机一次性 docker login（或导出 DOCKER_REPO_USERNAME / DOCKER_REPO_PASSWORD）；
//   变量清单与流水线设计见 devops/README.md。
//
//   参数：
//     ENV_NAME   目标环境 dev | test | uat | prod（默认 dev）
//     IMAGE_TAG  可选；留空时缺省取时间戳 yyyyMMddHHmmss
//
//   与 GitLab CI 的关系：双入口并存，均以 --env=<环境名> 调用同一脚本；同一时间
//   请只从一个入口发布，避免并发覆盖。
//
//   触发：仅 Jenkins 页面 Build Now 手动触发（无 Webhook、无定时轮询）。
//
//   节点：当前 agent any（如需固定到部署机，可改为 agent { label 'service-brain-agent' }），
//   需 git + bash + docker CLI（含 docker compose 插件）。不再使用 Jenkins 凭据：
//   镜像仓库口令由执行机 docker login 预认证（~/.docker/config.json），
//   应用口令存于部署机的 env/.env.<env>（严禁明文口令提交入库）。
// ==============================================================================

pipeline {

//     agent { label 'service-brain-agent' }
    agent any

    options {
        timestamps()
        // 等价 GitLab resource_group：串行发布，避免并发覆盖
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    parameters {
        choice(
            name: 'ENV_NAME',
            choices: ['dev', 'test', 'uat', 'prod'],
            description: '目标环境：应用配置为 env/.env.<env>（部署时挂载进容器）'
        )
        string(
            name: 'IMAGE_TAG',
            defaultValue: '',
            description: '可选；留空时缺省取时间戳 yyyyMMddHHmmss'
        )
    }

    stages {
        // ------------------------------------------------------------------------------
        // 一键发布：build -> package -> deploy 在同一 stage 内串接，
        // 镜像 tag 经 image_tag.txt 在脚本间传递，不产 artifact
        // ------------------------------------------------------------------------------
        stage('deploy-app') {
            steps {
                // 参数由 Jenkins 注入为环境变量；单引号 + shell 变量展开，
                // 规避 Groovy 字符串插值的注入风险与告警。
                // ENV_NAME 为空时缺省 dev（choice 参数在首次构建 / 无参触发时可能为空）
                sh 'bash devops/scripts/00-deploy-all.sh --env=${ENV_NAME:-dev}'
            }
        }
    }
}
