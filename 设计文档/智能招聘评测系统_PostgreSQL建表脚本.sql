-- ============================================================
-- 智能招聘评测系统 PostgreSQL 15+ 建表脚本（MVP 1.0）
-- 说明：
-- 1. 使用 UUID 主键；
-- 2. 复杂 AI 输出使用 JSONB；
-- 3. 关键状态使用 VARCHAR + CHECK，便于演进；
-- 4. 文件统一由 file_asset 管理；
-- 5. 关键人工动作通过 task_event_log 留痕。
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 更新时间触发器
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 1. 部门
CREATE TABLE department (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  department_code   VARCHAR(64) NOT NULL UNIQUE,
  department_name   VARCHAR(128) NOT NULL,
  parent_id         UUID REFERENCES department(id),
  status            VARCHAR(20) NOT NULL DEFAULT 'enabled'
                    CHECK (status IN ('enabled','disabled')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. 系统用户
CREATE TABLE app_user (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username          VARCHAR(64) NOT NULL UNIQUE,
  display_name      VARCHAR(128) NOT NULL,
  email             VARCHAR(256),
  mobile            VARCHAR(32),
  department_id     UUID REFERENCES department(id),
  role_code         VARCHAR(32) NOT NULL
                    CHECK (role_code IN ('hr','technical_reviewer','interviewer','admin')),
  status            VARCHAR(20) NOT NULL DEFAULT 'enabled'
                    CHECK (status IN ('enabled','disabled')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. 岗位
CREATE TABLE position (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  position_code         VARCHAR(64) NOT NULL UNIQUE,
  position_name         VARCHAR(128) NOT NULL,
  department_id         UUID REFERENCES department(id),
  job_level             VARCHAR(20) NOT NULL
                        CHECK (job_level IN ('junior','middle','senior','expert')),
  raw_job_description   TEXT NOT NULL,
  position_summary      TEXT,
  version               INTEGER NOT NULL DEFAULT 1,
  status                VARCHAR(20) NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','confirmed','disabled')),
  created_by            UUID REFERENCES app_user(id),
  confirmed_by          UUID REFERENCES app_user(id),
  confirmed_at          TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. 岗位能力项
CREATE TABLE position_skill (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  position_id             UUID NOT NULL REFERENCES position(id) ON DELETE CASCADE,
  skill_code              VARCHAR(64) NOT NULL,
  skill_name              VARCHAR(128) NOT NULL,
  skill_category          VARCHAR(64) NOT NULL,
  requirement_level       VARCHAR(20) NOT NULL
                          CHECK (requirement_level IN ('understand','master','proficient','expert')),
  weight_percent          NUMERIC(5,2) NOT NULL DEFAULT 0
                          CHECK (weight_percent >= 0 AND weight_percent <= 100),
  must_verify             BOOLEAN NOT NULL DEFAULT TRUE,
  preferred_methods       JSONB NOT NULL DEFAULT '[]'::jsonb,
  requirement_description TEXT,
  sort_no                 INTEGER NOT NULL DEFAULT 0,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(position_id, skill_code)
);

-- 5. 候选人
CREATE TABLE candidate (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_no        VARCHAR(64) NOT NULL UNIQUE,
  candidate_name      VARCHAR(128) NOT NULL,
  work_years          NUMERIC(4,1),
  education           VARCHAR(32),
  current_company     VARCHAR(128),
  current_position    VARCHAR(128),
  email               VARCHAR(256),
  mobile              VARCHAR(32),
  source_channel      VARCHAR(64),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. 文件资产
CREATE TABLE file_asset (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  storage_provider    VARCHAR(32) NOT NULL DEFAULT 'local',
  bucket_name         VARCHAR(128),
  object_key          VARCHAR(512) NOT NULL,
  original_file_name  VARCHAR(256) NOT NULL,
  file_ext            VARCHAR(20),
  mime_type           VARCHAR(128),
  file_size           BIGINT NOT NULL DEFAULT 0 CHECK (file_size >= 0),
  sha256              VARCHAR(64),
  security_level      VARCHAR(20) NOT NULL DEFAULT 'internal'
                      CHECK (security_level IN ('public','internal','confidential')),
  uploaded_by         UUID REFERENCES app_user(id),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. 简历
CREATE TABLE resume (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_id        UUID NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  file_id             UUID NOT NULL REFERENCES file_asset(id),
  resume_version      INTEGER NOT NULL DEFAULT 1,
  parse_status        VARCHAR(20) NOT NULL DEFAULT 'pending'
                      CHECK (parse_status IN ('pending','processing','success','failed')),
  resume_text         TEXT,
  parsed_profile      JSONB NOT NULL DEFAULT '{}'::jsonb,
  parse_error         TEXT,
  parsed_at           TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(candidate_id, resume_version)
);

-- 8. 招聘评测任务
CREATE TABLE recruitment_task (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_no                     VARCHAR(64) NOT NULL UNIQUE,
  task_name                   VARCHAR(256) NOT NULL,
  position_id                 UUID NOT NULL REFERENCES position(id),
  candidate_id                UUID NOT NULL REFERENCES candidate(id),
  resume_id                   UUID NOT NULL REFERENCES resume(id),
  department_id               UUID REFERENCES department(id),
  hr_owner_id                 UUID NOT NULL REFERENCES app_user(id),
  technical_owner_id          UUID NOT NULL REFERENCES app_user(id),
  planned_finish_at           TIMESTAMPTZ,
  overall_status              VARCHAR(32) NOT NULL DEFAULT 'draft'
                              CHECK (overall_status IN (
                                'draft','pending_analysis','pending_verification_confirmation',
                                'pending_question_review','pending_delivery','candidate_in_progress',
                                'pending_collection','pending_scoring','pending_report_confirmation',
                                'completed','cancelled'
                              )),
  regular_question_status     VARCHAR(32) NOT NULL DEFAULT 'not_generated'
                              CHECK (regular_question_status IN (
                                'not_generated','generated','reviewing','confirmed',
                                'exported','collected','scored'
                              )),
  development_task_status     VARCHAR(32) NOT NULL DEFAULT 'not_enabled'
                              CHECK (development_task_status IN (
                                'not_enabled','pending_generation','reviewing','pending_send',
                                'in_progress','collected','scored'
                              )),
  regular_weight_percent      NUMERIC(5,2) NOT NULL DEFAULT 40
                              CHECK (regular_weight_percent >= 0 AND regular_weight_percent <= 100),
  development_weight_percent  NUMERIC(5,2) NOT NULL DEFAULT 60
                              CHECK (development_weight_percent >= 0 AND development_weight_percent <= 100),
  created_by                  UUID REFERENCES app_user(id),
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 9. 简历项目经历
CREATE TABLE resume_project (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resume_id             UUID NOT NULL REFERENCES resume(id) ON DELETE CASCADE,
  project_name          VARCHAR(256),
  project_period        VARCHAR(128),
  project_role          VARCHAR(128),
  technologies          JSONB NOT NULL DEFAULT '[]'::jsonb,
  responsibilities      TEXT,
  claimed_result        TEXT,
  evidence_text         TEXT NOT NULL,
  evidence_page         INTEGER,
  sort_no               INTEGER NOT NULL DEFAULT 0,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 10. 简历能力分析
CREATE TABLE resume_skill_analysis (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id               UUID NOT NULL REFERENCES recruitment_task(id) ON DELETE CASCADE,
  position_skill_id     UUID REFERENCES position_skill(id),
  skill_code            VARCHAR(64) NOT NULL,
  skill_name            VARCHAR(128) NOT NULL,
  judgment              VARCHAR(32) NOT NULL
                        CHECK (judgment IN (
                          'basically_matched','needs_verification',
                          'description_doubtful','not_mentioned','irrelevant'
                        )),
  confidence            VARCHAR(16) NOT NULL
                        CHECK (confidence IN ('low','medium','high')),
  resume_evidence       JSONB NOT NULL DEFAULT '[]'::jsonb,
  risk_description      TEXT,
  suggested_method      VARCHAR(32)
                        CHECK (suggested_method IN (
                          'basic_question','qa_question','development_task','interview_followup'
                        )),
  ai_raw_result         JSONB NOT NULL DEFAULT '{}'::jsonb,
  manually_confirmed    BOOLEAN NOT NULL DEFAULT FALSE,
  confirmed_by          UUID REFERENCES app_user(id),
  confirmed_at          TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(task_id, skill_code)
);

-- 11. 待验证能力
CREATE TABLE verification_item (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id               UUID NOT NULL REFERENCES recruitment_task(id) ON DELETE CASCADE,
  skill_code            VARCHAR(64) NOT NULL,
  skill_name            VARCHAR(128) NOT NULL,
  source_type           VARCHAR(32) NOT NULL
                        CHECK (source_type IN ('position','resume_risk','position_and_resume','manual')),
  position_reason       TEXT,
  resume_reason         TEXT,
  priority              VARCHAR(16) NOT NULL
                        CHECK (priority IN ('low','medium','high')),
  suggested_method      VARCHAR(32) NOT NULL
                        CHECK (suggested_method IN (
                          'basic_question','qa_question','development_task','interview_followup'
                        )),
  selected_method       VARCHAR(32) NOT NULL
                        CHECK (selected_method IN (
                          'basic_question','qa_question','development_task','interview_followup'
                        )),
  status                VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','confirmed','removed')),
  sort_no               INTEGER NOT NULL DEFAULT 0,
  confirmed_by          UUID REFERENCES app_user(id),
  confirmed_at          TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 12. 普通题目集
CREATE TABLE regular_question_set (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id               UUID NOT NULL REFERENCES recruitment_task(id) ON DELETE CASCADE,
  set_name              VARCHAR(256) NOT NULL,
  version               INTEGER NOT NULL DEFAULT 1,
  question_count        INTEGER NOT NULL DEFAULT 0 CHECK (question_count >= 0),
  suggested_duration    INTEGER NOT NULL DEFAULT 30 CHECK (suggested_duration > 0),
  difficulty_level      VARCHAR(20) NOT NULL
                        CHECK (difficulty_level IN ('junior','middle','senior','expert')),
  status                VARCHAR(20) NOT NULL DEFAULT 'generated'
                        CHECK (status IN ('generated','reviewing','confirmed','archived')),
  review_owner_id       UUID REFERENCES app_user(id),
  reviewed_at           TIMESTAMPTZ,
  generation_config     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(task_id, version)
);

-- 13. 普通题目
CREATE TABLE regular_question (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question_set_id         UUID NOT NULL REFERENCES regular_question_set(id) ON DELETE CASCADE,
  verification_item_id    UUID REFERENCES verification_item(id),
  question_no             INTEGER NOT NULL,
  question_type           VARCHAR(20) NOT NULL
                          CHECK (question_type IN ('basic','qa')),
  title                   VARCHAR(256) NOT NULL,
  content                 TEXT NOT NULL,
  answer_requirement      TEXT,
  skill_code              VARCHAR(64) NOT NULL,
  skill_name              VARCHAR(128) NOT NULL,
  difficulty              VARCHAR(20) NOT NULL
                          CHECK (difficulty IN ('basic','medium','hard')),
  score                   NUMERIC(6,2) NOT NULL DEFAULT 0 CHECK (score >= 0),
  suggested_time_minutes  INTEGER CHECK (suggested_time_minutes > 0),
  reference_answer        JSONB NOT NULL DEFAULT '[]'::jsonb,
  scoring_points          JSONB NOT NULL DEFAULT '[]'::jsonb,
  generation_reason       TEXT NOT NULL,
  position_evidence       TEXT,
  resume_evidence         JSONB NOT NULL DEFAULT '[]'::jsonb,
  risk_performance        TEXT,
  follow_up_question      TEXT,
  review_status           VARCHAR(20) NOT NULL DEFAULT 'pending'
                          CHECK (review_status IN ('pending','confirmed','needs_revision','removed')),
  manually_edited         BOOLEAN NOT NULL DEFAULT FALSE,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(question_set_id, question_no)
);

-- 14. 现场开发题
CREATE TABLE development_task (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id               UUID NOT NULL REFERENCES recruitment_task(id) ON DELETE CASCADE,
  version               INTEGER NOT NULL DEFAULT 1,
  title                 VARCHAR(256) NOT NULL,
  business_background   TEXT NOT NULL,
  objective             TEXT NOT NULL,
  task_requirements     JSONB NOT NULL DEFAULT '[]'::jsonb,
  duration_days         INTEGER NOT NULL DEFAULT 3 CHECK (duration_days > 0),
  allow_llm             BOOLEAN NOT NULL DEFAULT TRUE,
  allow_internet        BOOLEAN NOT NULL DEFAULT TRUE,
  delivery_requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
  acceptance_criteria   JSONB NOT NULL DEFAULT '[]'::jsonb,
  candidate_instruction TEXT,
  reviewer_guide        TEXT,
  status                VARCHAR(24) NOT NULL DEFAULT 'reviewing'
                        CHECK (status IN ('reviewing','pending_send','in_progress','collected','scored','archived')),
  send_time             TIMESTAMPTZ,
  deadline              TIMESTAMPTZ,
  returned_at           TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(task_id, version)
);

-- 15. 候选人提交记录
CREATE TABLE candidate_submission (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id               UUID NOT NULL REFERENCES recruitment_task(id) ON DELETE CASCADE,
  submission_type       VARCHAR(20) NOT NULL
                        CHECK (submission_type IN ('regular','development')),
  question_set_id       UUID REFERENCES regular_question_set(id),
  development_task_id   UUID REFERENCES development_task(id),
  submitted_at          TIMESTAMPTZ,
  uploaded_by           UUID REFERENCES app_user(id),
  status                VARCHAR(20) NOT NULL DEFAULT 'submitted'
                        CHECK (status IN ('submitted','missing','needs_supplement','accepted')),
  integrity_note        TEXT,
  extracted_answers     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (
    (submission_type = 'regular' AND question_set_id IS NOT NULL AND development_task_id IS NULL)
    OR
    (submission_type = 'development' AND development_task_id IS NOT NULL AND question_set_id IS NULL)
  )
);

-- 16. 提交文件
CREATE TABLE submission_file (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  submission_id         UUID NOT NULL REFERENCES candidate_submission(id) ON DELETE CASCADE,
  file_id               UUID NOT NULL REFERENCES file_asset(id),
  file_role             VARCHAR(32) NOT NULL
                        CHECK (file_role IN (
                          'answer_sheet','source_code','readme','screenshot',
                          'design_doc','other'
                        )),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(submission_id, file_id)
);

-- 17. 普通题单题评分
CREATE TABLE question_score (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id               UUID NOT NULL REFERENCES recruitment_task(id) ON DELETE CASCADE,
  submission_id         UUID NOT NULL REFERENCES candidate_submission(id) ON DELETE CASCADE,
  question_id           UUID NOT NULL REFERENCES regular_question(id) ON DELETE CASCADE,
  max_score             NUMERIC(6,2) NOT NULL CHECK (max_score >= 0),
  ai_suggested_score    NUMERIC(6,2) CHECK (ai_suggested_score >= 0),
  ai_reason             TEXT,
  reviewer_score        NUMERIC(6,2) CHECK (reviewer_score >= 0),
  reviewer_comment      TEXT,
  final_score           NUMERIC(6,2) CHECK (final_score >= 0),
  confirmed_by          UUID REFERENCES app_user(id),
  confirmed_at          TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(submission_id, question_id)
);

-- 18. 现场开发题评分
CREATE TABLE development_score (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id               UUID NOT NULL REFERENCES recruitment_task(id) ON DELETE CASCADE,
  submission_id         UUID NOT NULL REFERENCES candidate_submission(id) ON DELETE CASCADE,
  development_task_id   UUID NOT NULL REFERENCES development_task(id) ON DELETE CASCADE,
  dimension_code        VARCHAR(64) NOT NULL,
  dimension_name        VARCHAR(128) NOT NULL,
  max_score             NUMERIC(6,2) NOT NULL CHECK (max_score >= 0),
  ai_suggested_score    NUMERIC(6,2) CHECK (ai_suggested_score >= 0),
  ai_reason             TEXT,
  reviewer_score        NUMERIC(6,2) CHECK (reviewer_score >= 0),
  reviewer_comment      TEXT,
  final_score           NUMERIC(6,2) CHECK (final_score >= 0),
  sort_no               INTEGER NOT NULL DEFAULT 0,
  confirmed_by          UUID REFERENCES app_user(id),
  confirmed_at          TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(submission_id, dimension_code)
);

-- 19. 最终评测报告
CREATE TABLE evaluation_report (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id               UUID NOT NULL REFERENCES recruitment_task(id) ON DELETE CASCADE,
  report_version        INTEGER NOT NULL DEFAULT 1,
  regular_score         NUMERIC(6,2),
  development_score     NUMERIC(6,2),
  final_score           NUMERIC(6,2),
  skill_evaluations     JSONB NOT NULL DEFAULT '[]'::jsonb,
  strengths             JSONB NOT NULL DEFAULT '[]'::jsonb,
  risks                 JSONB NOT NULL DEFAULT '[]'::jsonb,
  interview_focus       JSONB NOT NULL DEFAULT '[]'::jsonb,
  recommendation        VARCHAR(32)
                        CHECK (recommendation IN (
                          'next_round','focused_retest','not_next_round'
                        )),
  conclusion_text       TEXT,
  status                VARCHAR(20) NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','pending_confirmation','confirmed','archived')),
  generated_by_ai       BOOLEAN NOT NULL DEFAULT TRUE,
  confirmed_by          UUID REFERENCES app_user(id),
  confirmed_at          TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(task_id, report_version)
);

-- 20. 任务事件与审计日志
CREATE TABLE task_event_log (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id               UUID NOT NULL REFERENCES recruitment_task(id) ON DELETE CASCADE,
  event_type            VARCHAR(64) NOT NULL,
  operator_id           UUID REFERENCES app_user(id),
  from_status           VARCHAR(64),
  to_status             VARCHAR(64),
  event_data            JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================
-- 索引
-- =========================
CREATE INDEX idx_user_department_role
  ON app_user(department_id, role_code, status);

CREATE INDEX idx_position_department_status
  ON position(department_id, status);

CREATE INDEX idx_position_skill_position_sort
  ON position_skill(position_id, sort_no);

CREATE INDEX idx_resume_candidate_status
  ON resume(candidate_id, parse_status);

CREATE INDEX idx_task_status_owner
  ON recruitment_task(overall_status, hr_owner_id, technical_owner_id);

CREATE INDEX idx_task_candidate_position
  ON recruitment_task(candidate_id, position_id);

CREATE INDEX idx_task_updated_at
  ON recruitment_task(updated_at DESC);

CREATE INDEX idx_resume_analysis_task_judgment
  ON resume_skill_analysis(task_id, judgment);

CREATE INDEX idx_verification_task_status_priority
  ON verification_item(task_id, status, priority);

CREATE INDEX idx_question_set_task_status
  ON regular_question_set(task_id, status, version DESC);

CREATE INDEX idx_question_set_review
  ON regular_question(question_set_id, review_status, question_no);

CREATE INDEX idx_development_task_task_status
  ON development_task(task_id, status, version DESC);

CREATE INDEX idx_submission_task_type_status
  ON candidate_submission(task_id, submission_type, status);

CREATE INDEX idx_question_score_task
  ON question_score(task_id, confirmed_at);

CREATE INDEX idx_development_score_task
  ON development_score(task_id, confirmed_at);

CREATE INDEX idx_report_task_status
  ON evaluation_report(task_id, status, report_version DESC);

CREATE INDEX idx_task_event_log_task_time
  ON task_event_log(task_id, created_at DESC);

-- JSONB 检索索引（数据量上来后再启用也可以）
CREATE INDEX idx_position_skill_methods_gin
  ON position_skill USING GIN(preferred_methods);

CREATE INDEX idx_resume_parsed_profile_gin
  ON resume USING GIN(parsed_profile);

CREATE INDEX idx_question_resume_evidence_gin
  ON regular_question USING GIN(resume_evidence);

CREATE INDEX idx_report_skill_eval_gin
  ON evaluation_report USING GIN(skill_evaluations);

-- =========================
-- updated_at 触发器
-- =========================
DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'department','app_user','position','position_skill','candidate','resume',
    'recruitment_task','resume_skill_analysis','verification_item',
    'regular_question_set','regular_question','development_task',
    'candidate_submission','question_score','development_score','evaluation_report'
  ]
  LOOP
    EXECUTE format('
      CREATE TRIGGER trg_%I_updated_at
      BEFORE UPDATE ON %I
      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    ', t, t);
  END LOOP;
END;
$$;