<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">解析任务</h2>
        <p class="page-header__desc">
          对接后端 /api/v1/kb/parse-jobs：页面自助上传并提交解析（与契约 #01 同一条流水线），查看任务状态机、原始请求快照与解析产物
        </p>
      </div>
      <div class="page-header__actions">
        <el-button type="primary" size="small" @click="openCreate">
          <el-icon><Upload /></el-icon>
          <span>新建解析</span>
        </el-button>
        <el-button size="small" :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <SectionCard flush>
      <div class="kb-toolbar">
        <el-form :inline="true" :model="query" size="small" @submit.prevent>
          <el-form-item label="任务 ID">
            <el-input v-model="query.uuid" placeholder="模糊匹配" clearable style="width: 190px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item label="资源类型">
            <el-select v-model="query.resource_type" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in resourceTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="任务状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 110px">
              <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="失败码">
            <el-input v-model="query.error_code" placeholder="精确匹配" clearable style="width: 130px" @keyup.enter="search" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="search">查询</el-button>
            <el-button @click="reset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="list" size="small" style="width: 100%">
        <el-table-column align="center" prop="uuid" label="任务 ID" min-width="200" show-overflow-tooltip />
        <el-table-column align="center" label="资源类型" width="100">
          <template #default="{ row }">{{ resourceTypeLabel(row.resource_type) }}</template>
        </el-table-column>
        <el-table-column align="center" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column align="center" label="进度" min-width="150">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress ?? 0"
              :status="progressStatus(row)"
              :stroke-width="10"
              size="small"
            />
          </template>
        </el-table-column>
        <el-table-column align="center" prop="error_code" label="失败码" width="140" show-overflow-tooltip />
        <el-table-column align="center" prop="error_message" label="失败说明" min-width="180" show-overflow-tooltip />
        <el-table-column align="center" label="创建时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.created_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="更新时间" width="165">
          <template #default="{ row }">{{ formatDateTime(row.updated_time) }}</template>
        </el-table-column>
        <el-table-column align="center" label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无解析任务，点击右上角「新建解析」上传文件" :image-size="80" />
        </template>
      </el-table>

      <div class="kb-pagination">
        <el-pagination
          :current-page="pagination.page_no"
          :page-size="pagination.page_size"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          size="small"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </SectionCard>

    <el-dialog v-model="createVisible" title="新建解析" width="620px" destroy-on-close>
      <el-form :model="createForm" label-width="96px" size="small" @submit.prevent>
        <el-form-item label="文件" required>
          <el-upload
            ref="uploadRef"
            v-model:file-list="createFileList"
            class="kb-upload"
            drag
            :auto-upload="false"
            :limit="1"
            accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.markdown,.csv,.log,.json,.yaml,.yml,.html,.htm"
            :on-exceed="handleFileExceed"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">将文件拖到此处，或<em>点击选择</em></div>
            <template #tip>
              <div class="el-upload__tip">
                单文件不超过 10MB；PDF/Office 需服务端启用 MinerU 解析器，文本类后缀始终可用
              </div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item label="资源类型">
          <el-radio-group v-model="createForm.resource_type">
            <el-radio v-for="item in resourceTypeOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="生成切片">
          <el-switch v-model="createForm.chunk" :disabled="createForm.resource_type === 'faq'" />
          <span class="kb-form__tip">
            {{ createForm.resource_type === 'faq' ? 'FAQ 按问答提取，忽略此项' : '按标题层级切分正文' }}
          </span>
        </el-form-item>
        <el-form-item label="FAQ 语言">
          <el-input
            v-model="createForm.language"
            maxlength="16"
            :disabled="createForm.resource_type !== 'faq'"
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="FAQ 提示词">
          <el-input
            v-model="createForm.prompt_text"
            type="textarea"
            :rows="3"
            maxlength="4000"
            show-word-limit
            :disabled="createForm.resource_type !== 'faq'"
            placeholder="FAQ 必填：写清提取口径与面向对象，问答须完整自足"
          />
        </el-form-item>
        <el-form-item label="模型键">
          <el-input v-model="createForm.model_key" maxlength="64" placeholder="留空使用默认模型" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="createVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="creating" @click="submitCreate">提交解析</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" size="1180px" destroy-on-close>
      <template #header>
        <div class="kb-drawer__head">
          <span class="kb-drawer__title">解析任务详情</span>
          <el-button
            v-if="canReslice"
            size="small"
            type="warning"
            plain
            :loading="retrying"
            @click="resliceChunks"
          >
            重新切片
          </el-button>
          <el-button
            v-if="canRetry"
            size="small"
            type="primary"
            plain
            :loading="retrying"
            @click="retryFromStep(failedStep?.step || null)"
          >
            {{ retryLabel }}
          </el-button>
        </div>
      </template>
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="任务 ID" :span="2">{{ detail.uuid }}</el-descriptions-item>
            <el-descriptions-item label="资源类型">{{ resourceTypeLabel(detail.resource_type) }}</el-descriptions-item>
            <el-descriptions-item label="执行轮次">第 {{ detail.attempt ?? 1 }} 轮</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTagType(detail.status)" size="small" effect="light">{{ statusLabel(detail.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="进度">
              <el-progress
                :percentage="detail.progress ?? 0"
                :status="progressStatus(detail)"
                :stroke-width="10"
                size="small"
                style="width: 100%"
              />
            </el-descriptions-item>
            <el-descriptions-item label="失败码">{{ detail.error_code || '-' }}</el-descriptions-item>
            <el-descriptions-item label="失败说明" :span="2">{{ detail.error_message || '-' }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDateTime(detail.created_time) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatDateTime(detail.updated_time) }}</el-descriptions-item>
          </el-descriptions>

          <el-tabs v-model="activeTab" class="kb-detail__tabs">
            <el-tab-pane label="请求快照" name="fileMeta">
              <JsonViewer :data="detail.file_meta" title="file_meta（原始请求快照）" placeholder="任务未写入请求快照" />
            </el-tab-pane>

            <!-- 步骤台账：按执行轮次倒序，失败那一步可原地续跑 -->
            <el-tab-pane label="流水线步骤" name="steps">
              <div class="kb-step__bar">
                <span class="kb-step__hint">{{ stepTimelineHint }}</span>
                <el-button link type="primary" size="small" :loading="stepsLoading" @click="refreshSteps">
                  刷新步骤
                </el-button>
              </div>

              <section v-for="group in stepAttempts" :key="group.attempt" class="kb-step__group">
                <header class="kb-step__head">
                  <span class="kb-step__title">第 {{ group.attempt }} 轮</span>
                  <el-tag v-if="group.attempt === (detail.attempt ?? 1)" size="small" type="primary" effect="plain">
                    当前
                  </el-tag>
                  <el-tag v-if="group.failed" size="small" type="danger" effect="plain">
                    {{ group.failed }} 步失败
                  </el-tag>
                  <span class="kb-step__meta">{{ group.steps.length }} 步</span>
                </header>
                <el-table :data="group.steps" size="small" style="width: 100%">
                  <el-table-column label="步骤" min-width="180">
                    <template #default="{ row }">
                      <span class="kb-step__name">{{ stepLabel(row.step) }}</span>
                      <el-tag v-if="row.status === 'failed' && row.fatal === false" size="small" type="info" effect="plain">
                        降级
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="状态" width="96" align="center">
                    <template #default="{ row }">
                      <el-tag :type="stepTagType(row.status)" size="small" effect="light">
                        {{ stepStatusLabel(row.status) }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="耗时" width="92" align="center">
                    <template #default="{ row }">{{ formatDuration(row.duration_ms) }}</template>
                  </el-table-column>
                  <el-table-column label="开始时间" width="165" align="center">
                    <template #default="{ row }">{{ formatDateTime(row.started_time) }}</template>
                  </el-table-column>
                  <el-table-column label="说明" min-width="220" show-overflow-tooltip>
                    <template #default="{ row }">{{ stepSummary(row) }}</template>
                  </el-table-column>
                  <el-table-column label="操作" width="118" align="center">
                    <template #default="{ row }">
                      <el-button
                        v-if="row.status === 'failed'"
                        link
                        type="primary"
                        size="small"
                        :disabled="!canRetry"
                        :loading="retrying"
                        @click="retryFromStep(row.step)"
                      >
                        从此步重跑
                      </el-button>
                      <span v-else>-</span>
                    </template>
                  </el-table-column>
                  <template #empty>
                    <el-empty description="暂无步骤记录：任务尚未开始执行" :image-size="70" />
                  </template>
                </el-table>
              </section>
              <el-empty v-if="!stepAttempts.length" description="暂无步骤记录：任务尚未开始执行" :image-size="80" />
            </el-tab-pane>

            <el-tab-pane label="解析产物" name="result">
              <template v-if="detail.result">
                <el-descriptions :column="2" border size="small" class="kb-detail__block">
                  <el-descriptions-item label="关联任务">{{ detail.result.job_id }}</el-descriptions-item>
                  <el-descriptions-item label="产物哈希">{{ detail.result.content_hash }}</el-descriptions-item>
                  <el-descriptions-item label="写入时间">{{ formatDateTime(detail.result.created_time) }}</el-descriptions-item>
                  <el-descriptions-item label="切片数量">{{ (detail.result.chunks || []).length }}</el-descriptions-item>
                  <el-descriptions-item label="页图数量" :span="2">{{ pageItems.length }}</el-descriptions-item>
                </el-descriptions>

                <!-- MinerU 产物归档：全文 Markdown / 版面 JSON / 内嵌图片，供重解析与审计回取 -->
                <div class="kb-artifact kb-detail__block">
                  <header class="kb-artifact__head">
                    <span class="kb-artifact__title">MinerU 产物</span>
                    <span class="kb-artifact__meta">{{ artifactMeta }}</span>
                  </header>
                  <template v-if="detail.artifact_urls">
                    <div class="kb-artifact__links">
                      <el-button
                        v-if="detail.artifact_urls.markdown_url"
                        link
                        type="primary"
                        size="small"
                        @click="openArtifact(detail.artifact_urls.markdown_url)"
                      >
                        <el-icon><Document /></el-icon>
                        <span>full.md（解析正文）</span>
                      </el-button>
                      <el-button
                        v-if="detail.artifact_urls.content_list_url"
                        link
                        type="primary"
                        size="small"
                        @click="openArtifact(detail.artifact_urls.content_list_url)"
                      >
                        <el-icon><Tickets /></el-icon>
                        <span>content_list.json（版面块）</span>
                      </el-button>
                      <span class="kb-artifact__count">图片 {{ detail.artifact_urls.image_count || 0 }} 张</span>
                    </div>
                    <div v-if="detail.artifact_urls.images?.length" class="kb-artifact__images">
                      <el-image
                        v-for="image in detail.artifact_urls.images"
                        :key="image.object_key"
                        :src="image.image_url"
                        :preview-src-list="artifactImageUrls"
                        fit="contain"
                        preview-teleported
                        hide-on-click-modal
                        class="kb-artifact__thumb"
                      />
                    </div>
                  </template>
                  <el-empty v-else :description="artifactEmptyHint" :image-size="60" />
                </div>

                <!-- 契约 03.1 等价入口：把当前产物（含人工修订）的切片集合原样写成一版正式知识 -->
                <div class="kb-publish kb-detail__block">
                  <div class="kb-publish__info">
                    <span class="kb-publish__title">写入知识平台</span>
                    <span class="kb-publish__desc">{{ publishHint }}</span>
                  </div>
                  <div class="kb-publish__actions">
                    <el-tag v-if="detail.result.publication_id" type="success" effect="plain">
                      已写入 · {{ detail.result.publication_id }}
                    </el-tag>
                    <el-button
                      v-else
                      type="primary"
                      size="small"
                      :disabled="!resultChunks.length"
                      :loading="publishDialog.saving"
                      @click="openPublishDialog"
                    >
                      写入知识平台
                    </el-button>
                  </div>
                </div>

                <JsonViewer :data="detail.result.suggested" title="suggested（AI 建议元数据）" placeholder="无建议元数据" class="kb-detail__block" />
                <JsonViewer :data="detail.result.warnings" title="warnings（解析告警）" placeholder="无解析告警" class="kb-detail__block" />
                <el-collapse class="kb-detail__block">
                  <el-collapse-item title="chunks 原始 JSON（排障用）" name="rawChunks">
                    <JsonViewer :data="detail.result.chunks" title="chunks（切片数组）" placeholder="无切片产物" max-height="360px" />
                  </el-collapse-item>
                </el-collapse>

                <!-- 页图与切片合并成三栏：左侧目录定位、中间原页对照、右侧切块正文 -->
                <div class="kb-merge kb-detail__block">
                  <section class="kb-merge__panel">
                    <header class="kb-merge__head">
                      <span class="kb-merge__title">目录 / 页码</span>
                      <span class="kb-merge__meta">{{ pageGroups.length }} 项</span>
                    </header>
                    <div class="kb-merge__body kb-toc">
                      <button
                        v-for="group in pageGroups"
                        :key="group.key"
                        type="button"
                        class="kb-toc__item"
                        :class="{ 'is-active': group.key === activeGroup?.key }"
                        @click="activePageKey = group.key"
                      >
                        <span class="kb-toc__page">{{ group.pageText }}</span>
                        <span class="kb-toc__title">{{ group.summary }}</span>
                      </button>
                      <el-empty v-if="!pageGroups.length" description="无切片产物" :image-size="60" />
                    </div>
                  </section>

                  <section class="kb-merge__panel">
                    <header class="kb-merge__head">
                      <span class="kb-merge__title">{{ activePage ? `第 ${activePage.page_no} 页` : '页图预览' }}</span>
                      <span class="kb-merge__meta">{{ sourceFileName }}</span>
                    </header>
                    <div class="kb-merge__body kb-merge__body--preview">
                      <el-image
                        v-if="activePage"
                        :key="`${activePage.page_no}-${previewRetryKey}`"
                        :src="activePage.image_url"
                        :preview-src-list="pageUrls"
                        :initial-index="activePageIndex"
                        fit="contain"
                        preview-teleported
                        hide-on-click-modal
                        class="kb-merge__image"
                      >
                        <template #error>
                          <div class="kb-merge__error">
                            <span>页面图片加载失败或预览地址已过期</span>
                            <el-button size="small" @click.stop="previewRetryKey += 1">重试</el-button>
                          </div>
                        </template>
                      </el-image>
                      <el-empty v-else :description="previewHint" :image-size="70" />
                    </div>
                  </section>

                  <section class="kb-merge__panel">
                    <header class="kb-merge__head">
                      <span class="kb-merge__title">Chunk（{{ pageChunks.length }}）</span>
                      <span class="kb-merge__meta">{{ activeGroup?.pageText }}</span>
                    </header>
                    <div class="kb-merge__body">
                      <article v-for="chunk in pageChunks" :key="chunk.seq" class="kb-chunk">
                        <div class="kb-chunk__head">
                          <el-tag size="small" type="info" effect="plain">#{{ chunk.seq }}</el-tag>
                          <el-tag v-if="chunk.edited" size="small" type="warning" effect="plain">已修订</el-tag>
                          <span class="kb-chunk__meta">{{ chunkPageText(chunk) }} · {{ (chunk.content || '').length }} 字</span>
                          <el-button link type="primary" size="small" class="kb-chunk__edit" @click="openChunkEditor(chunk)">
                            <el-icon><Edit /></el-icon>
                            <span>编辑</span>
                          </el-button>
                        </div>
                        <h4 class="kb-chunk__title">{{ chunk.heading || chunkExcerpt(chunk, 40) }}</h4>
                        <!-- 切片正文按 Markdown 渲染：表格、列表、代码块与最终入库形态一致 -->
                        <div class="kb-chunk__content">
                          <MarkdownView :content="chunk.content" />
                        </div>
                      </article>
                      <el-empty v-if="!pageChunks.length" description="该页无切片" :image-size="60" />
                    </div>
                  </section>
                </div>
              </template>
              <el-empty v-else description="任务尚未产出解析结果" :image-size="80" />
            </el-tab-pane>

            <el-tab-pane label="关联发布单元" name="publications">
              <el-alert :title="relatedHint" type="info" :closable="false" show-icon />
              <el-table
                v-loading="related.loading"
                :data="related.list"
                size="small"
                style="width: 100%"
                class="kb-detail__block"
              >
                <el-table-column align="center" prop="publication_id" label="发布单元" min-width="170" show-overflow-tooltip />
                <el-table-column align="center" prop="title" label="标题" min-width="180" show-overflow-tooltip />
                <el-table-column align="center" prop="resource_id" label="资源 ID" min-width="140" show-overflow-tooltip />
                <el-table-column align="center" label="状态" width="90">
                  <template #default="{ row }">{{ row.status || '-' }}</template>
                </el-table-column>
                <el-table-column align="center" label="当前版本" width="90">
                  <template #default="{ row }">v{{ row.current_revision ?? 0 }}</template>
                </el-table-column>
                <template #empty>
                  <el-empty description="没有按 doc_id 匹配到发布单元" :image-size="70" />
                </template>
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </template>
        <el-empty v-else description="未获取到解析任务详情" :image-size="80" />
      </div>
    </el-drawer>

    <!-- 切片编辑：左侧写 Markdown 源码，右侧实时预览（与列表共用同一个渲染器） -->
    <el-dialog v-model="chunkEditor.visible" title="编辑切片" width="1000px" destroy-on-close>
      <div class="kb-editor">
        <section class="kb-editor__pane">
          <header class="kb-editor__head">
            <span>Markdown 源码</span>
            <span class="kb-editor__meta">#{{ chunkEditor.seq }} · {{ chunkEditor.content.length }} 字</span>
          </header>
          <el-input
            v-model="chunkEditor.content"
            type="textarea"
            :rows="17"
            maxlength="20000"
            resize="none"
            placeholder="支持 Markdown：标题、列表、表格、代码块"
            class="kb-editor__input"
          />
        </section>
        <section class="kb-editor__pane">
          <header class="kb-editor__head">
            <span>实时预览</span>
          </header>
          <div class="kb-editor__preview">
            <MarkdownView v-if="chunkEditor.content.trim()" :content="chunkEditor.content" />
            <el-empty v-else description="暂无内容" :image-size="60" />
          </div>
        </section>
      </div>
      <template #footer>
        <div class="kb-editor__footer">
          <span class="kb-editor__hint">保存后仅覆盖该条切片正文，产物哈希与其余切片不变</span>
          <el-button size="small" @click="chunkEditor.visible = false">取消</el-button>
          <el-button
            size="small"
            type="primary"
            :loading="chunkEditor.saving"
            :disabled="!canSaveChunkEdit"
            @click="saveChunkEdit"
          >
            保存
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 写入知识平台：切片由服务端取产物（含人工修订），这里只补发布单元展示字段 -->
    <el-dialog v-model="publishDialog.visible" title="写入知识平台" width="620px" destroy-on-close>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        :title="`将入库 ${resultChunks.length} 条切片（${editedChunkCount} 条已修订），不再二次切分`"
        class="kb-publish__alert"
      />
      <el-form label-width="88px">
        <el-form-item label="发布单元">
          <el-input v-model="publishForm.publication_id" maxlength="64" placeholder="留空自动生成" />
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="publishForm.title" maxlength="200" placeholder="留空取原文件名" />
        </el-form-item>
        <el-form-item label="租户 ID">
          <el-input v-model="publishForm.tenant_id" maxlength="64" placeholder="默认 gsi-default" />
        </el-form-item>
        <el-form-item label="资源 ID">
          <el-input v-model="publishForm.resource_id" maxlength="64" placeholder="留空取任务 doc_id（管理台上传没有）" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="publishForm.category" maxlength="64" placeholder="如：it / hr / 产品" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="publishForm.tags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="回车创建标签"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="维护人">
          <el-input v-model="publishForm.owner" maxlength="64" placeholder="留空不记录" />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="publishForm.summary" type="textarea" :rows="3" placeholder="留空不记录" />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="kb-publish__footer">
          <span class="kb-publish__hint">写入后到发布单元页修订新版本；本产物不可重复写入</span>
          <el-button size="small" @click="publishDialog.visible = false">取消</el-button>
          <el-button size="small" type="primary" :loading="publishDialog.saving" @click="submitPublish">
            确认写入
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox, genFileId } from 'element-plus'

import { getPublicationList } from '@/api/module_kb/publication'
import {
  getParseJobDetail,
  getParseJobList,
  getParseJobSteps,
  publishParseChunks,
  retryParseJob,
  submitParseJob,
  updateParseChunk,
} from '@/api/module_kb/parseJob'
import JsonViewer from '@/components/common/JsonViewer.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { useTable } from '@/hooks/useTable'
import { formatDateTime } from '@/utils/format'

defineOptions({ name: 'KbParseJobView' })

const resourceTypeOptions = [
  { value: 'document', label: '文档' },
  { value: 'faq', label: 'FAQ' },
]
const statusOptions = [
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '解析中' },
  { value: 'succeeded', label: '成功' },
  { value: 'failed', label: '失败' },
]
const STATUS_TAG = { queued: 'info', running: 'warning', succeeded: 'success', failed: 'danger' }
const PROGRESS_STATUS = { succeeded: 'success', failed: 'exception' }

function statusLabel(status) {
  return statusOptions.find((item) => item.value === status)?.label ?? (status || '-')
}
function statusTagType(status) {
  return STATUS_TAG[status] || 'info'
}
function resourceTypeLabel(type) {
  return resourceTypeOptions.find((item) => item.value === type)?.label ?? (type || '-')
}
function progressStatus(row) {
  return PROGRESS_STATUS[row.status]
}

/** 空值不下发：后端将空串视为「不筛选」 */
function buildQueryParams(params) {
  const result = { page_no: params.page_no, page_size: params.page_size }
  ;['uuid', 'resource_type', 'status', 'error_code'].forEach((key) => {
    if (params[key]) result[key] = params[key]
  })
  return result
}

const { loading, list, total, pagination, query, load, search, reset, handlePageChange, handleSizeChange } = useTable(
  (params) => getParseJobList(buildQueryParams(params)),
  {
    defaultQuery: { uuid: '', resource_type: '', status: '', error_code: '' },
  },
)

// ── 新建解析（管理端自助上传） ─────────────────────────────────────
const createVisible = ref(false)
const creating = ref(false)
const uploadRef = ref()
const createFileList = ref([])
const createForm = reactive({
  resource_type: 'document',
  chunk: true,
  language: 'zh-CN',
  prompt_text: '',
  model_key: '',
})

function openCreate() {
  createFileList.value = []
  Object.assign(createForm, {
    resource_type: 'document',
    chunk: true,
    language: 'zh-CN',
    prompt_text: '',
    model_key: '',
  })
  createVisible.value = true
}

/** 只收单文件：超出时用新选中的文件替换已选文件（el-upload 默认会忽略第二次选择） */
function handleFileExceed(files) {
  uploadRef.value?.clearFiles()
  const file = files[0]
  file.uid = genFileId()
  uploadRef.value?.handleStart(file)
}

/** multipart 提交：文件 + 解析选项；解析在响应后由后台任务执行，结果看列表状态 */
async function submitCreate() {
  const file = createFileList.value[0]?.raw
  if (!file) {
    ElMessage.warning('请先选择待解析文件')
    return
  }
  const data = new FormData()
  data.append('file', file)
  data.append('resource_type', createForm.resource_type)
  data.append('chunk', createForm.chunk ? 'true' : 'false')
  data.append('language', createForm.language || 'zh-CN')
  if (createForm.prompt_text) data.append('prompt_text', createForm.prompt_text)
  if (createForm.model_key) data.append('model_key', createForm.model_key)

  creating.value = true
  try {
    const job = await submitParseJob(data)
    ElMessage.success(`解析任务已受理${job?.uuid ? `：${job.uuid}` : ''}`)
    createVisible.value = false
    load()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    creating.value = false
  }
}

// ── 详情 ──────────────────────────────────────────────────────────
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const activeTab = ref('fileMeta')

// ── 流水线步骤台账（跟踪 / 失败原因 / 从失败步续跑） ────────────────────
// 步骤口径与后端 pipeline 一致：faq 与 chunk 按资源类型互斥，不会同时出现
const STEP_LABELS = {
  extract: '解析（MinerU → 文本）',
  artifacts: '产物归档（对象存储）',
  faq: 'FAQ 抽取',
  chunk: '正文切片',
  pages: '页图渲染',
  suggest: '建议元数据',
  persist: '产物落库',
}
const STEP_STATUS = {
  running: { label: '运行中', tag: 'warning' },
  succeeded: { label: '成功', tag: 'success' },
  failed: { label: '失败', tag: 'danger' },
  skipped: { label: '跳过', tag: 'info' },
}

const retrying = ref(false)
const stepsLoading = ref(false)

function stepLabel(step) {
  return STEP_LABELS[step] || step || '-'
}
function stepStatusLabel(status) {
  return STEP_STATUS[status]?.label || status || '-'
}
function stepTagType(status) {
  return STEP_STATUS[status]?.tag || 'info'
}

/** 耗时：毫秒原样、过一秒换成秒，避免长步骤写成「183000 ms」 */
function formatDuration(ms) {
  const value = Number(ms)
  if (ms === null || ms === undefined || !Number.isFinite(value)) return '-'
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`
}

/** 一步的说明：失败/跳过看原因，成功看轻量元数据（解析器/页数/条数） */
function stepSummary(row) {
  if (row.error_message) return row.error_message
  const detail = row.detail || {}
  const parts = []
  if (detail.parser) parts.push(detail.parser)
  if (detail.pages) parts.push(`${detail.pages} 页`)
  if (detail.chars) parts.push(`${detail.chars} 字`)
  if (detail.images !== undefined) parts.push(`${detail.images} 张图`)
  if (detail.section_chunks !== undefined) parts.push(`${detail.section_chunks} 个段落`)
  if (detail.chunks !== undefined) parts.push(`${detail.chunks} 条切片`)
  if (detail.title) parts.push(detail.title)
  if (detail.markdown) parts.push('已归档')
  return parts.length ? parts.join(' · ') : '-'
}

/** 按执行轮次分组：最新一轮在前（重跑后先看这一轮），组内保持后端执行顺序 */
const stepAttempts = computed(() => {
  const groups = new Map()
  ;(detail.value?.steps || []).forEach((row) => {
    const attempt = row.attempt ?? 1
    let group = groups.get(attempt)
    if (!group) {
      group = { attempt, steps: [], failed: 0 }
      groups.set(attempt, group)
    }
    group.steps.push(row)
    if (row.status === 'failed') group.failed += 1
  })
  return [...groups.values()].sort((a, b) => b.attempt - a.attempt)
})

/** 当前轮次里失败的那一步：抽屉顶部的「重跑」直接以它作起点 */
const failedStep = computed(() => {
  const attempt = detail.value?.attempt ?? 1
  return (detail.value?.steps || []).find((row) => (row.attempt ?? 1) === attempt && row.status === 'failed') || null
})

/** 只有失败与运行中可重跑：成功的重跑会覆盖已审核产物（后端同样 409） */
const canRetry = computed(() => ['failed', 'running'].includes(detail.value?.status))

/**
 * 已成功的任务可以只重切片：换切块口径时不必重新解析原件（FAQ 没有 chunk 步，不适用）。
 * 产物里若有人工修订过的切片，后端会 409 拒绝，由拦截器统一提示。
 */
const canReslice = computed(
  () =>
    detail.value?.status === 'succeeded' &&
    detail.value?.resource_type !== 'faq' &&
    detail.value?.file_meta?.options?.chunk !== false,
)

const retryLabel = computed(() => (failedStep.value ? `从「${stepLabel(failedStep.value.step)}」重跑` : '重新排队'))

const stepTimelineHint = computed(() => {
  if (!detail.value) return ''
  if (detail.value.status === 'failed') return '已失败：从失败步骤重跑，之前的步骤复用台账快照，不重新解析文档'
  if (detail.value.status === 'running') return '执行中：最后一行「运行中」即当前正在执行的步骤'
  return '每一步一行；重跑会追加新的执行轮次，历史记录保留'
})

/** 对象存储预签名地址只对当前会话有效，每次打开详情重新取（后端detail已重签） */
const artifactImageUrls = computed(() => (detail.value?.artifact_urls?.images || []).map((image) => image.image_url))

const artifactMeta = computed(() => {
  const artifacts = detail.value?.result?.artifacts
  if (!artifacts) return '未归档'
  const parts = [artifacts.parser || 'MinerU', `${artifacts.image_count ?? 0} 张图片`]
  if (artifacts.content_list_key) parts.push('含版面 JSON')
  return parts.join(' · ')
})

const artifactEmptyHint = computed(() =>
  detail.value?.result?.artifacts
    ? '产物已归档，但对象存储未启用或预签名失败，暂时取不到下载地址'
    : '未归档 MinerU 产物：对象存储未启用，或本次解析未走 MinerU',
)

/** 产物下载：预签名 URL 直开新窗口（md 直接展示、json 随浏览器策略、图片可放大） */
function openArtifact(url) {
  if (url) window.open(url, '_blank', 'noopener')
}

/** 只刷新步骤台账：轮询时不动产物与选中页，避免列表页跳变 */
async function refreshSteps() {
  const jobId = detail.value?.uuid
  if (!jobId || stepsLoading.value) return
  stepsLoading.value = true
  try {
    detail.value.steps = await getParseJobSteps(jobId)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    stepsLoading.value = false
  }
}

/** 从指定步骤重跑：不传则由后端取上一轮第一处失败的步骤 */
async function retryFromStep(step) {
  const jobId = detail.value?.uuid
  if (!jobId || retrying.value) return
  retrying.value = true
  try {
    const out = await retryParseJob(jobId, step ? { from_step: step } : {})
    Object.assign(detail.value, {
      status: out.status,
      progress: out.progress,
      attempt: out.attempt,
      error_code: out.error_code,
      error_message: out.error_message,
    })
    ElMessage.success(
      step === 'chunk'
        ? `已受理重新切片（第 ${out.attempt} 轮）`
        : `已受理重跑（第 ${out.attempt} 轮）${step ? `，从「${stepLabel(step)}」继续` : ''}`,
    )
    activeTab.value = 'steps'
    load()
    // 新一轮的步骤行由后台任务逐条写入，先同步一次已有记录
    await refreshSteps()
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    retrying.value = false
  }
}

/**
 * 只重切片：后端从解析快照重算切片，不重新调解析器。
 * 会整体替换产物里的切片，所以先弹窗确认（已发布的知识不受影响，需重新写入才生效）。
 */
async function resliceChunks() {
  if (!detail.value?.uuid || retrying.value) return
  try {
    await ElMessageBox.confirm(
      '将按当前切块口径重新切分这份文档，不重新解析原件。产物里的切片会被整体替换；若已有切片被人工修订，后端会拒绝本次操作。',
      '重新切片',
      { type: 'warning', confirmButtonText: '开始重切', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await retryFromStep('chunk')
}

/** 页图预览数组：只保留带 image_url 的项，供缩略图与放大预览共用 */
const pageItems = computed(() => (detail.value?.result?.pages || []).filter((page) => page?.image_url))
const pageUrls = computed(() => pageItems.value.map((page) => page.image_url))

// ── 页图与切片的联动：左栏按页去重，中栏跟随该页页图，右栏列出该页切片 ────────
const resultChunks = computed(() => detail.value?.result?.chunks || [])
/** 目录选中项（页分组 key）；null 时按第一页兜底，避免打开详情后三栏全空 */
const activePageKey = ref(null)
/** 预签名地址失效或瞬时失败时改 key 重建 el-image，让它重新发一次请求 */
const previewRetryKey = ref(0)

/**
 * 目录按起始页去重：同一页的多条切片并成一条目录项（一页一条）。
 * 页码缺失的切片单独归为「未标注页码」一组，不伪造归属。
 */
const pageGroups = computed(() => {
  const groups = []
  const index = new Map()
  resultChunks.value.forEach((chunk) => {
    const pageNo = chunk.page_from ?? null
    const key = pageNo === null ? 'unpaged' : `page-${pageNo}`
    let group = index.get(key)
    if (!group) {
      group = { key, pageNo, pageText: chunkPageText(chunk), chunks: [] }
      index.set(key, group)
      groups.push(group)
    }
    group.chunks.push(chunk)
  })
  return groups.map((group) => ({
    ...group,
    // 目录第二行：条数 + 首条标题，兼顾「这页切了几块」与「这页讲什么」
    summary: `${group.chunks.length} 条${group.chunks[0]?.heading ? ` · ${group.chunks[0].heading}` : ''}`,
  }))
})

const activeGroup = computed(
  () => pageGroups.value.find((group) => group.key === activePageKey.value) || pageGroups.value[0] || null,
)

/** 右栏只列当前页的切片：目录已按页归并，这里直接取组内成员 */
const pageChunks = computed(() => activeGroup.value?.chunks || [])

/** 选中页：严格按页码取（超出渲染上限则留空，不拿第一页充数）；未标注页码的组不预览页图 */
const activePage = computed(() => {
  const pageNo = activeGroup.value?.pageNo
  if (pageNo === null || pageNo === undefined) return null
  return pageItems.value.find((page) => page.page_no === pageNo) || null
})

const activePageIndex = computed(() => Math.max(pageItems.value.indexOf(activePage.value), 0))

/** 中栏空态文案：区分「整个任务无页图」「该组无页码」「选中页超出渲染上限」 */
const previewHint = computed(() => {
  if (!pageItems.value.length) return '无页图预览：仅 PDF 且服务启用页图能力时生成'
  if (!activeGroup.value?.pageNo) return '该组切片未标注页码，无法定位页图'
  return `第 ${activeGroup.value.pageNo} 页无页图（可能超出单文档渲染上限）`
})

const sourceFileName = computed(() => detail.value?.file_meta?.file_name || detail.value?.result?.file_name || '-')

/** 页码文案：解析产物只在定位到页时回填 page_from/page_to，两者相等即单页 */
function chunkPageText(chunk) {
  if (chunk?.page_from === undefined || chunk?.page_from === null) return '未标注页码'
  const from = chunk.page_from
  const to = chunk.page_to
  return to === undefined || to === null || to === from ? `第 ${from} 页` : `第 ${from}-${to} 页`
}

/** 无 heading 时的兜底标题：压平空白并去掉 Markdown 标记后截断，避免目录里出现 `##` */
function chunkExcerpt(chunk, max = 24) {
  const text = String(chunk?.content || '')
    .replace(/^\s*#{1,6}\s*/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return '（空切片）'
  return text.length > max ? `${text.slice(0, max)}…` : text
}

// ── 切片编辑（Markdown 源码 + 实时预览，保存后只回写这一条） ──────────
const chunkEditor = reactive({ visible: false, seq: null, content: '', original: '', saving: false })

/** 内容未改或清空时不允许提交：空正文入产物后检索侧会拿到空白切片 */
const canSaveChunkEdit = computed(() => {
  const text = chunkEditor.content
  return !chunkEditor.saving && text.trim().length > 0 && text !== chunkEditor.original
})

function openChunkEditor(chunk) {
  chunkEditor.seq = chunk.seq
  chunkEditor.content = chunk.content || ''
  chunkEditor.original = chunk.content || ''
  chunkEditor.visible = true
}

async function saveChunkEdit() {
  const jobId = detail.value?.uuid
  if (!jobId || chunkEditor.seq === null) return
  chunkEditor.saving = true
  try {
    const out = await updateParseChunk(jobId, { seq: chunkEditor.seq, content: chunkEditor.content })
    // 局部回写：只替换这一条，避免整份详情重拉导致选中页与滚动位置丢失
    const hit = resultChunks.value.find((chunk) => chunk.seq === out.seq)
    if (hit) {
      hit.content = out.content
      hit.edited = out.edited
      hit.edited_time = out.edited_time
    }
    ElMessage.success(`切片 #${out.seq} 已保存`)
    chunkEditor.visible = false
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    chunkEditor.saving = false
  }
}

// ── 写入知识平台（03.1 等价：产物切片原样入库，不再二次切分） ─────────
const publishDialog = reactive({ visible: false, saving: false })
const publishForm = reactive({
  publication_id: '',
  title: '',
  tenant_id: 'gsi-default',
  resource_id: '',
  category: '',
  tags: [],
  owner: '',
  summary: '',
})

/** 已修订条数：写入前先告诉用户「改过的正文会一起入库」，避免以为写的是原始解析结果 */
const editedChunkCount = computed(() => resultChunks.value.filter((chunk) => chunk.edited).length)

const publishHint = computed(() => {
  const result = detail.value?.result
  if (!result) return ''
  const base = `${resultChunks.value.length} 条切片 · ${editedChunkCount.value} 条已修订`
  return result.publication_id
    ? `${base} · 写入于 ${formatDateTime(result.published_time)}`
    : `${base} · 按产物原样入库，一份产物只能写一次`
})

/** 标题缺省：原文件名去扩展名（与后端缺省口径一致） */
function defaultPublishTitle() {
  const base = String(sourceFileName.value || '').split(/[\\/]/).pop() || ''
  const dot = base.lastIndexOf('.')
  return dot > 0 ? base.slice(0, dot) : base
}

function openPublishDialog() {
  Object.assign(publishForm, {
    publication_id: '',
    title: defaultPublishTitle(),
    tenant_id: 'gsi-default',
    resource_id: detail.value?.file_meta?.doc_id || '',
    category: '',
    tags: [],
    owner: '',
    summary: '',
  })
  publishDialog.visible = true
}

async function submitPublish() {
  const jobId = detail.value?.uuid
  if (!jobId || publishDialog.saving) return
  publishDialog.saving = true
  try {
    const out = await publishParseChunks(jobId, {
      publication_id: publishForm.publication_id.trim() || null,
      title: publishForm.title.trim() || null,
      tenant_id: publishForm.tenant_id.trim() || null,
      resource_id: publishForm.resource_id.trim() || null,
      category: publishForm.category.trim() || null,
      summary: publishForm.summary.trim() || null,
      tags: publishForm.tags,
      owner: publishForm.owner.trim() || null,
    })
    // 局部回写「已写入」标识：不重拉详情，保留当前选中页与滚动位置
    const result = detail.value?.result
    if (result) {
      result.publication_id = out.publication_id
      result.published_time = out.published_time
    }
    publishDialog.visible = false
    ElMessage.success(`已写入知识平台：${out.publication_id}（${out.chunk_count} 条切片）`)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    publishDialog.saving = false
  }
}

// ── 关联发布单元（反向溯源：用 file_meta.doc_id 比对 publication.resource_id）───
// 声明顺序：必须在 activeTab 之后（watch 在 setup 阶段立即读取它），且在 openDetail 之前
const related = reactive({
  loading: false,
  data: null,
  list: [],
})

/** 契约任务才有 doc_id；管理台上传的任务没有这个字段，因此不存在可匹配的发布单元 */
const relatedHint = computed(() => {
  const docId = detail.value?.file_meta?.doc_id
  return docId
    ? `按 doc_id=${docId} 匹配发布单元的 resource_id：线索，不构成强关联`
    : '该任务没有 doc_id（管理台上传），不参与发布单元匹配'
})

async function loadRelated() {
  const docId = detail.value?.file_meta?.doc_id
  if (!docId) {
    related.list = []
    return
  }
  related.loading = true
  try {
    const result = await getPublicationList({ resource_id: docId, page_size: 20 })
    related.data = result
    related.list = result.items || []
  } catch {
    related.list = []
  } finally {
    related.loading = false
  }
}

watch(activeTab, (name) => {
  if (name === 'publications' && related.data === null) {
    loadRelated()
  }
})

async function openDetail(row) {
  detail.value = null
  detailVisible.value = true
  detailLoading.value = true
  // 失败的任务直接落到步骤页签：失败原因与「从此步重跑」都在那里
  activeTab.value = row.status === 'failed' ? 'steps' : 'fileMeta'
  // 三栏联动状态随任务切换重置，避免沿用上一条任务的选中页
  activePageKey.value = null
  previewRetryKey.value = 0
  chunkEditor.visible = false
  publishDialog.visible = false
  retrying.value = false
  stepsLoading.value = false
  related.data = null
  related.list = []
  try {
    detail.value = await getParseJobDetail(row.uuid)
  } catch {
    // 请求拦截器已统一提示错误
  } finally {
    detailLoading.value = false
  }
}
</script>

<style scoped lang="scss">
.kb-toolbar {
  padding: 20px 26px 4px;
}

.kb-upload {
  width: 100%;

  :deep(.el-upload) {
    width: 100%;
  }
}

.kb-form__tip {
  margin-left: 10px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-pagination {
  @include flex($justify: flex-end);
  padding: 12px 26px 20px;
}

.kb-detail__tabs {
  margin-top: 16px;
}

.kb-detail__block {
  margin-top: 12px;
}

.kb-drawer__head {
  @include flex($justify: space-between, $gap: 12px);
  padding-right: 24px;
}

.kb-drawer__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text-primary);
}

/* 步骤台账：一轮一块，失败步骤行内续跑 */
.kb-step__bar {
  @include flex($justify: space-between, $gap: 12px);
  padding: 6px 0 10px;
}

.kb-step__hint {
  @include ellipsis;
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-step__group {
  margin-bottom: 14px;

  &:last-child {
    margin-bottom: 0;
  }
}

.kb-step__head {
  @include flex($gap: 8px);
  padding: 6px 0 8px;
}

.kb-step__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-primary);
}

.kb-step__meta {
  margin-left: auto;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.kb-step__name {
  margin-right: 8px;
}

/* MinerU 产物：下载链接行 + 图片缩略墙 */
.kb-artifact {
  padding: 10px 12px;
  border: 1px solid var(--app-border-color);
  border-radius: var(--app-radius-sm);
  background-color: var(--app-card-bg);

  &__head {
    @include flex($justify: space-between, $gap: 12px);
    padding-bottom: 8px;
    border-bottom: 1px solid var(--app-border-color);
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__meta {
    @include ellipsis;
    max-width: 60%;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__links {
    @include flex($gap: 10px);
    flex-wrap: wrap;
    padding: 8px 0;
  }

  &__count {
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__images {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
    gap: 8px;
  }

  &__thumb {
    width: 100%;
    height: 84px;
    border: 1px solid var(--app-border-color);
    border-radius: var(--app-radius-sm);
    background-color: var(--app-tint-bg);
    cursor: zoom-in;

    :deep(img) {
      width: 100%;
      height: 100%;
      object-fit: contain;
    }
  }
}

/* 解析产物三栏：目录 / 页图 / 切片，三栏等高，各自内部滚动 */
.kb-merge {
  display: grid;
  grid-template-columns: 212px minmax(0, 1fr) 336px;
  gap: 12px;
  height: 620px;

  &__panel {
    @include flex($direction: column);
    min-width: 0;
    overflow: hidden;
    border: 1px solid var(--app-border-color);
    border-radius: var(--app-radius-sm);
    background-color: var(--app-card-bg);
  }

  &__head {
    @include flex($justify: space-between, $gap: 8px);
    flex: none;
    padding: 10px 12px;
    border-bottom: 1px solid var(--app-border-color);
    background-color: var(--app-tint-bg);
  }

  &__title {
    @include ellipsis;
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__meta {
    @include ellipsis;
    flex: none;
    max-width: 60%;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 10px 12px;

    &--preview {
      @include flex(center, center);
      padding: 12px;
      background-color: var(--app-tint-bg);
    }
  }

  &__image {
    width: 100%;
    height: 100%;
    cursor: zoom-in;

    :deep(img) {
      width: 100%;
      height: 100%;
      object-fit: contain;
    }
  }

  &__error {
    @include flex(center, center, 10px, column);
    height: 100%;
    padding: 0 8px;
    font-size: 12px;
    color: var(--app-text-secondary);
    text-align: center;
  }
}

.kb-toc {
  @include flex($direction: column, $gap: 2px);
  padding: 6px;

  &__item {
    @include flex($direction: column, $gap: 2px);
    width: 100%;
    padding: 8px 10px;
    border: 0;
    border-radius: 8px;
    background-color: transparent;
    cursor: pointer;
    text-align: left;
    transition: background-color 0.18s ease;

    &:hover {
      background-color: var(--app-tint-bg);
    }

    &.is-active {
      background-color: rgba(163, 43, 104, 0.1);

      .kb-toc__page {
        font-weight: 600;
        color: var(--app-brand);
      }
    }
  }

  &__page {
    @include ellipsis;
    font-size: 13px;
    color: var(--app-text-primary);
  }

  &__title {
    @include ellipsis;
    width: 100%;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

.kb-chunk {
  margin-bottom: 10px;
  padding: 10px 12px;
  border: 1px solid var(--app-border-color);
  border-radius: var(--app-radius-sm);

  &:last-child {
    margin-bottom: 0;
  }

  &__head {
    @include flex($gap: 8px);
  }

  &__meta {
    @include ellipsis;
    flex: 1;
    min-width: 0;
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__edit {
    flex: none;
    margin-left: auto;
  }

  &__title {
    margin: 8px 0 6px;
    font-size: 13px;
    font-weight: 600;
    line-height: 1.5;
    color: var(--app-text-primary);
  }

  &__content {
    margin: 0;
    padding: 8px 10px;
    border-radius: 8px;
    background-color: var(--app-tint-bg);
    word-break: break-word;

    /* 右栏只有 336px，沿用全局 14px 会把一行挤成两三个字 */
    :deep(.markdown-view) {
      font-size: 12px;
      line-height: 1.7;
    }
  }
}

/* 切片编辑：左写右预览，两栏等高 */
.kb-editor {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  height: 440px;

  &__pane {
    @include flex($direction: column);
    min-width: 0;
    overflow: hidden;
    border: 1px solid var(--app-border-color);
    border-radius: var(--app-radius-sm);
    background-color: var(--app-card-bg);
  }

  &__head {
    @include flex($justify: space-between, $gap: 8px);
    flex: none;
    padding: 8px 12px;
    border-bottom: 1px solid var(--app-border-color);
    background-color: var(--app-tint-bg);
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__meta {
    flex: none;
    font-size: 12px;
    font-weight: 400;
    color: var(--app-text-secondary);
  }

  &__input {
    flex: 1;
    min-height: 0;

    :deep(.el-textarea__inner) {
      height: 100%;
      font-family: var(--app-font-mono, monospace);
      font-size: 12px;
      line-height: 1.7;
      box-shadow: none;

      &:focus {
        box-shadow: 0 0 0 1px var(--el-color-primary) inset;
      }
    }
  }

  &__preview {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 10px 12px;
  }

  &__footer {
    @include flex($justify: flex-end, $gap: 8px);
  }

  &__hint {
    margin-right: auto;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}

/* 写入知识平台：一条工具条，未写入时给按钮、已写入时只剩状态标签 */
.kb-publish {
  @include flex($justify: space-between, $gap: 12px);
  padding: 10px 14px;
  border: 1px dashed var(--app-border-color);
  border-radius: var(--app-radius-sm);
  background-color: var(--app-tint-bg);

  &__info {
    @include flex($direction: column, $gap: 2px);
    min-width: 0;
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__desc {
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__actions {
    @include flex($gap: 8px);
    flex: none;
  }

  &__alert {
    margin-bottom: 12px;
  }

  &__footer {
    @include flex($justify: flex-end, $gap: 8px);
  }

  &__hint {
    margin-right: auto;
    font-size: 12px;
    color: var(--app-text-secondary);
  }
}
</style>
