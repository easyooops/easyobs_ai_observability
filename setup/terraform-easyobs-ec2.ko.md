# EasyObs on EC2 (Terraform)

**영문 원문:** [`setup/terraform-easyobs-ec2.md`](terraform-easyobs-ec2.md)

**저장소 루트**에서 실행합니다. 두 가지 토폴로지가 있습니다.

| 토폴로지 | 용도 | Terraform 디렉터리 |
|----------|------|---------------------|
| **single** | EC2 1대: API + Web + Postgres(compose) | `setup/ec2/terraform/single/` |
| **cluster** | ALB + API 리더 EC2 + API 워커 ASG + Web EC2 + RDS Postgres + EFS(blob) | `setup/ec2/terraform/cluster/` |

**워커 스케일링(OSS):** 별도 큐 워커(Celery/Dramatiq)는 없습니다. 수평 확장 = 동일 이미지로 API 컨테이너를 더 띄움; 수집(ingest)과 자동 규칙 평가가 부하를 나눕니다. **알람:** `EASYOBS_ALARM_ENABLED=true`는 인스턴스 **하나**에만 두어야 합니다(중복 알림 방지). `cluster/`에서는 Terraform user_data가 리더에만 알람을 켭니다.

PowerShell: `.sh`와 `.ps1`이 모두 있으면 `.ps1` 스크립트를 사용합니다.

---

## 사전 요구 사항

- [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) + 자격 증명
- 저장소 체크아웃: Terraform이 EasyObs 트리를 S3에 압축 업로드하고, EC2가 부팅 시 내려받아 `docker build`합니다. EasyObs 이미지는 **ECR 푸시가 필요 없습니다**.

EC2는 베이스 이미지(`python:3.12-slim`, `node:20-alpine`, `postgres:16`, …)를 인터넷에서 받아야 합니다. 폐쇄망 배포는 `setup/offline/`을 사용합니다(§4 참고).

---

## 1. 단일 노드

### 1-1. 변수

```powershell
Copy-Item .\setup\ec2\terraform\single\terraform.tfvars.example `
          .\setup\ec2\terraform\single\terraform.tfvars
```

```bash
cp setup/ec2/terraform/single/terraform.tfvars.example \
   setup/ec2/terraform/single/terraform.tfvars
```

주요 키: `aws_region`, `instance_type`, `allow_easyobs_cidr`, `enable_data_volume`, `easyobs_api_image_tag`, `easyobs_web_image_tag`, `seed_mock_data`. `terraform.tfvars`는 gitignore됩니다. Postgres 비밀번호와 JWT 시크릿은 Terraform이 생성합니다.

### 1-2. 적용(Apply)

**Windows (PowerShell)** — 저장소 루트에서:

```powershell
.\setup\ec2\terraform\single\tf-apply.ps1
```

**macOS / Linux / Git Bash:**

```bash
bash ./setup/ec2/terraform/single/tf-apply.sh
```

흐름: `terraform init` → `validate` → `plan -out tfplan`; `y`로 확인 후 `terraform apply tfplan`.

### 1-3. 출력(Outputs)

```powershell
cd .\setup\ec2\terraform\single
terraform output
terraform output -raw postgres_password
terraform output -raw jwt_secret
```

| 출력 | 의미 |
|------|------|
| `easyobs_api_url` | API 베이스(`/docs`, `/healthz`, `/v1/...`) |
| `easyobs_web_url` | Next.js 콘솔 |
| `public_ip` | EIP |
| `stage_bucket` | S3 스테이징 버킷(디버그) |

부트스트랩: `/var/log/easyobs-bootstrap.log`, SSM. `easyobs-api`가 healthy 될 때까지 대략 5–10분.

첫 가입 → 슈퍼 관리자(`administrator` 조직). `seed_mock_data=true`는 카탈로그에 트레이스가 없을 때만 데모 트레이스를 시드합니다.

### 1-4. 삭제(Destroy)

```powershell
.\setup\ec2\terraform\single\tf-delete.ps1
```

```bash
bash ./setup/ec2/terraform/single/tf-delete.sh
```

---

## 2. 클러스터

ALB + 리더 EC2 + 워커 ASG + Web EC2 + RDS + EFS. 워커는 `api_worker_desired_capacity` 또는 ASG로 스케일합니다.

```
                                  ┌─ Web EC2 (Next.js, 3000)
   Internet → ALB :80 ─┬─ /  ─────┘
                        │
                        └─ /v1/* /otlp/* /healthz /docs /openapi.json
                                 → API target group
                                      ├─ Leader EC2 (alarm=true)
                                      └─ Worker ASG (alarm=false, N)
                                           │
                                           └─ RDS Postgres + EFS (blob)
```

### 2-1. 변수

```bash
cp setup/ec2/terraform/cluster/terraform.tfvars.example \
   setup/ec2/terraform/cluster/terraform.tfvars
```

| 변수 | 기본값 | 비고 |
|------|--------|------|
| `instance_type` | `t3.medium` | 모든 EC2(리더/워커/웹) |
| `api_worker_min_size` | `1` | ASG 최소 |
| `api_worker_desired_capacity` | `2` | 초기 워커 수 |
| `api_worker_max_size` | `6` | ASG 최대 |
| `rds_instance_class` | `db.t3.medium` | |
| `rds_allocated_storage` | `50` GB | |
| `rds_multi_az` | `false` | 운영에서는 `true` 권장 |
| `rds_skip_final_snapshot` | `true` | 운영에서는 `false` 권장 |
| `allow_alb_cidr` | `0.0.0.0/0` | ALB 인바운드 |

### 2-2. 적용(Apply)

```powershell
.\setup\ec2\terraform\cluster\tf-apply.ps1
```

```bash
bash ./setup/ec2/terraform/cluster/tf-apply.sh
```

대략 소요: **약 15–20분**(RDS, NAT, ASG, ALB).

### 2-3. 출력(Outputs)

```bash
cd setup/ec2/terraform/cluster
terraform output
terraform output -raw rds_password
terraform output -raw jwt_secret
```

| 출력 | 의미 |
|------|------|
| `easyobs_url` | 진입점(ALB DNS, HTTP) |
| `alb_dns_name` | ALB DNS(예: Route53 CNAME) |
| `api_leader_instance_id` | 알람이 켜진 EC2 |
| `api_worker_asg_name` | 워커 ASG |
| `rds_endpoint` | Postgres |
| `efs_id` | 공유 blob EFS |

### 2-4. 워커 스케일

```bash
aws autoscaling set-desired-capacity \
  --auto-scaling-group-name "$(terraform output -raw api_worker_asg_name)" \
  --desired-capacity 4
```

또는 `terraform.tfvars`의 `api_worker_desired_capacity`를 바꾼 뒤 재적용합니다. 새 워커는 user_data 완료 후 ALB 타깃 그룹에 등록됩니다.

### 2-5. 삭제(Destroy)

```powershell
.\setup\ec2\terraform\cluster\tf-delete.ps1
```

```bash
bash ./setup/ec2/terraform/cluster/tf-delete.sh
```

`rds_skip_final_snapshot=false`이면 destroy 시 RDS 최종 스냅샷이 생성됩니다.

---

## 3. 스모크 테스트

```bash
# single
EASYOBS_URL=$(terraform -chdir=setup/ec2/terraform/single output -raw easyobs_api_url)
curl -fsS "$EASYOBS_URL/healthz"

# cluster
EASYOBS_URL=$(terraform -chdir=setup/ec2/terraform/cluster output -raw easyobs_url)
curl -fsS "$EASYOBS_URL/healthz"
```

OTLP 경로 동작 확인(빈 본문 → 400이어도 OK):

```bash
curl -i -X POST -H "Content-Type: application/x-protobuf" \
  "$EASYOBS_URL/otlp/v1/traces" --data-binary ""
```

## 4. 폐쇄망(Air-gapped) 배포

EC2 Terraform은 이미지 pull을 위해 아웃바운드 인터넷을 가정합니다. 폐쇄망: Docker 이미지 tar 번들 + `setup/offline/` 스크립트.

- 빌드: `setup/offline/build-bundle.sh` / `build-bundle.ps1`
- 가이드: `setup/offline/README.md`

```bash
# 1) 빌드 머신(온라인)
./setup/offline/build-bundle.sh --output ./dist/easyobs-bundle

# 2) ./dist/easyobs-bundle 을 대상에 복사한 뒤:
cd /path/to/easyobs-bundle
./load-bundle.sh
./deploy-single.sh
# 또는
./deploy-cluster.sh single-host 4
./deploy-cluster.sh multi-host worker
```

멀티 호스트 오프라인: 공유 NFS/EFS blob, 외부 Postgres, 내부 LB; 스크립트는 `EASYOBS_DATABASE_URL`, `EASYOBS_JWT_SECRET`, `EASYOBS_BLOB_HOST_DIR` 등을 받습니다.

> **DuckDB + Parquet (v0.2+):** 모든 배포 모드에서 아래 두 환경 변수를 `.env`에 추가해야 합니다.
>
> ```bash
> EASYOBS_STORAGE_FORMAT=parquet   # 권장. ndjson=레거시
> EASYOBS_QUERY_ENGINE=duckdb      # 권장. legacy=Python 루프
> ```
>
> S3/Azure/GCS blob 사용 시 `EASYOBS_BLOB_PROVIDER`, `EASYOBS_BLOB_BUCKET` 등은 `setup/compose/env.sample`을 참고해 설정합니다.

---

## 5. LLM Judge — AWS Bedrock IAM 구성

EC2 인스턴스에서 AWS Bedrock Judge를 쓰려면:

1. **IAM 인스턴스 프로파일**에 `bedrock:InvokeModel` 권한을 부여합니다:
   ```json
   {
     "Effect": "Allow",
     "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
     "Resource": "arn:aws:bedrock:*::foundation-model/*"
   }
   ```
2. Terraform `cluster/`의 EC2 역할에 위 정책을 붙이면, 컨테이너 안에서 `AWS_PROFILE`/`AWS_ACCESS_KEY_ID` **없이도** boto3가 Instance Metadata Service(IMDS)로 자동 인증합니다.
3. 로컬/Docker 환경에서는 `.env`에 `AWS_PROFILE=default`(네임드 프로파일) 또는 `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`를 설정합니다.
4. 리전은 Judge 모델 등록 시 `connection.aws_region`으로 지정합니다(기본 `us-east-1`).

> **기타 프로바이더(OpenAI/Anthropic/Google/Azure):**  
> `.env`에 `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `AZURE_OPENAI_API_KEY` 등을 설정합니다.  
> 전체 목록은 `setup/compose/env.sample`의 "LLM Judge" 섹션을 참고하세요.

---

## 6. 삭제 시 유의사항

- 스택은 **Terraform destroy**를 권장합니다; 콘솔에서 EC2만 지우면 VPC/EIP/ALB/ASG/EFS 등이 고아 리소스로 남을 수 있습니다.
- **State:** destroy는 현재 디렉터리의 `terraform.tfstate`에 있는 리소스를 사용합니다. state 분실 시 AWS에서 수동 정리가 필요합니다.
- **S3 스테이지 버킷:** `force_destroy=true`이면 destroy 시 객체가 제거됩니다.
- **EFS / RDS:** destroy 전 백업. `rds_skip_final_snapshot=false`일 때 RDS 최종 스냅샷.
- 미리보기: 스택 디렉터리에서 `terraform plan -destroy`.

---

## 경로

| 항목 | 경로 |
|------|------|
| 소스(개발) | 저장소 루트 |
| API Dockerfile | `setup/images/api/Dockerfile` |
| Web Dockerfile | `setup/images/web/Dockerfile` |
| Compose | `setup/compose/` |
| Terraform single | `setup/ec2/terraform/single/` |
| Terraform cluster | `setup/ec2/terraform/cluster/` |
| 오프라인 번들 | `setup/offline/` |
| Setup 개요 | `setup/README.md` |
