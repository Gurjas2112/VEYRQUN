{{- define "vq.fullname" -}}
{{- printf "%s" .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{- define "vq.labels" -}}
app.kubernetes.io/name: veyrqun
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "vq.selector" -}}
app.kubernetes.io/name: veyrqun
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "vq.image" -}}
{{- $tag := default .root.Chart.AppVersion .root.Values.image.tag -}}
{{- if .root.Values.image.registry -}}
{{ .root.Values.image.registry }}/{{ .name | trimPrefix "veyrqun/" }}:{{ $tag }}
{{- else -}}
{{ .name }}:{{ $tag }}
{{- end -}}
{{- end -}}

{{- define "vq.secretName" -}}
{{- default (printf "%s-secrets" (include "vq.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "vq.svc" -}}
{{ include "vq.fullname" .root }}-{{ .name }}
{{- end -}}

{{/* Env shared by every control-plane-image container (API, audit writer, migrations). */}}
{{- define "vq.cpEnv" -}}
- name: PG_APP_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: postgres-app-password, optional: true } }
- name: PG_OWNER_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: postgres-owner-password, optional: true } }
{{- if .Values.postgres.bundled }}
- name: DATABASE_URL
  value: "postgresql+psycopg://veyrqun_app:$(PG_APP_PASSWORD)@{{ include "vq.fullname" . }}-postgres:5432/veyrqun"
- name: MIGRATION_DATABASE_URL
  value: "postgresql+psycopg://veyrqun_owner:$(PG_OWNER_PASSWORD)@{{ include "vq.fullname" . }}-postgres:5432/veyrqun"
{{- else }}
- name: DATABASE_URL
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: database-url } }
- name: MIGRATION_DATABASE_URL
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: migration-database-url } }
{{- end }}
{{- if .Values.redis.bundled }}
- name: REDIS_URL
  value: "redis://{{ include "vq.fullname" . }}-redis:6379/0"
{{- else }}
- name: REDIS_URL
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: redis-url } }
{{- end }}
- name: VEYRQUN_ENV
  value: {{ .Values.controlPlane.env | quote }}
- name: VEYRQUN_SIMULATOR
  value: "0"
- name: VEYRQUN_MASTER_KEY
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: master-key } }
- name: VEYRQUN_LOOKUP_KEY
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: lookup-key } }
- name: BUNDLE_SERVICE_TOKEN
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: bundle-service-token } }
- name: METRICS_TOKEN
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: metrics-token } }
- name: OIDC_CLIENT_SECRET
  valueFrom: { secretKeyRef: { name: {{ include "vq.secretName" . }}, key: oidc-client-secret } }
- name: ALLOWED_ORIGINS
  value: {{ .Values.publicUrl | quote }}
- name: APP_BASE_URL
  value: {{ .Values.publicUrl | quote }}
- name: OIDC_REDIRECT_URI
  value: "{{ .Values.publicUrl }}/api/auth/callback"
- name: SESSION_COOKIE_SECURE
  value: {{ hasPrefix "https://" .Values.publicUrl | quote }}
- name: GATEWAY_URL
  value: "http://{{ include "vq.fullname" . }}-gateway:8080"
- name: LOG_LEVEL
  value: {{ .Values.controlPlane.logLevel | quote }}
- name: AUDIT_ANCHOR_INTERVAL_S
  value: {{ .Values.auditWriter.anchorIntervalS | quote }}
{{- if .Values.demo.enabled }}
- name: DEMO_MODE
  value: "1"
- name: DEMO_EXPIRES_AT
  value: {{ required "demo.expiresAt is required when demo.enabled" .Values.demo.expiresAt | quote }}
- name: AUDIT_DRAIN_ON_READ
  value: "1"
{{- end }}
{{- range $k, $v := .Values.controlPlane.extraEnv }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "vq.waitMigrated" -}}
- name: wait-for-migrations
  image: {{ include "vq.image" (dict "root" . "name" .Values.image.names.controlPlane) }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  # Starts only when the schema is at the head revision (the migration Job runs them as the owner role).
  command: ["sh", "-c", "until [ \"$(alembic current 2>/dev/null | awk '{print $1}')\" = \"$(alembic heads | awk '{print $1}')\" ]; do echo waiting for migrations; sleep 3; done"]
  env: {{- include "vq.cpEnv" . | nindent 4 }}
  securityContext: {{- toYaml .Values.containerSecurityContext | nindent 4 }}
  volumeMounts: [{ name: tmp, mountPath: /tmp }]
{{- end -}}
