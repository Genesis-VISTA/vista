{{/*
Common labels
*/}}
{{- define "vista.labels" -}}
app: {{ .Chart.Name }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "vista.selectorLabels" -}}
app: {{ .Chart.Name }}
{{- end }}

{{/*
Name of the app secrets Kubernetes Secret
*/}}
{{- define "vista.secretName" -}}
vista-secrets
{{- end }}

{{/*
Name of the OIDC credentials Secret (created by ExternalSecret or expected to exist)
*/}}
{{- define "vista.oidcSecretName" -}}
vista-oidc-credentials
{{- end }}
