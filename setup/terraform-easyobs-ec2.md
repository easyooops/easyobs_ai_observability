# EasyObs EC2 (Terraform) — 로컬 실행 가이드

레포 **루트**(`oss_observability/`)에서 아래 명령을 실행합니다. EasyObs
(`docs/comparison/03.develop/easyobs/`) 를 운영용 AWS 환경에 한 번에 배포할
수 있도록 두 가지 토폴로지를 제공합니다.

| 토폴로지 | 용도 | Terraform 디렉터리 |
|----------|------|----------------------|
| **single** | 단일 EC2 1대에 API + Web + Postgres compose 풀스택 | `docs/comparison/03.develop/easyobs/setup/ec2/terraform/single/` |
| **cluster** | ALB + API leader EC2 + API worker ASG + Web EC2 + RDS Postgres + EFS (수평 확장) | `docs/comparison/03.develop/easyobs/setup/ec2/terraform/cluster/` |

> **요구사항 3 “Worker 노드 확장 클러스터”에 대한 진단**
>
> 현재 EasyObs OSS 코드는 큐 기반 워커 분리(Celery/Dramatiq 등)가 구현돼
> 있지 않습니다. 그러나 ingest/HTTP API/auto-rule 평가가 모두 단일 비동기
> 프로세스 안에서 실행되므로, **API 컨테이너를 동일 이미지로 N대 수평
> 확장하면** ingest·자동 평가 부하가 자연스럽게 분산됩니다. 단, in-process
> alarm evaluator 는 한 인스턴스에서만 켜야 알람이 중복 발송되지 않으므로
> `cluster/` 토폴로지에서는 leader EC2 1대만 `EASYOBS_ALARM_ENABLED=true`
> 로 띄웁니다 (Terraform user_data 가 자동 처리).

PowerShell 사용자는 어떤 명령이든 `.sh` 대신 `.ps1` 을 그대로 쓰면 됩니다.

---

## 사전 준비

- [Terraform](https://developer.hashicorp.com/terraform/install) 1.5 이상
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) + 자격 증명(`aws configure`, 환경 변수, SSO 등)
- 워크스페이스 루트(`oss_observability/`) 체크아웃 — Terraform 이 EasyObs 소스 트리를
  zip 으로 패키징해 S3 에 올리고, EC2 가 부팅 시 그 zip 을 다운로드해
  `docker build` 합니다. 즉 **EasyObs 이미지를 ECR 에 미리 푸시할 필요가 없습니다.**

> 단, EC2 가 docker base image (`python:3.12-slim`, `node:20-alpine`,
> `postgres:16` 등) 를 인터넷에서 받아야 하므로 **온라인 모드** 라는 점에 주의.
> 폐쇄망에서는 아래 “4. 폐쇄망(오프라인) 배포” 섹션을 따라가세요.

---

## 1. 단일 노드 (single)

가장 빠른 운영 시작. 한 EC2 안에서 API + Web + Postgres 가 docker compose 로
한꺼번에 떠오릅니다.

### 1-1. 변수 설정

```powershell
Copy-Item .\docs\comparison\03.develop\easyobs\setup\ec2\terraform\single\terraform.tfvars.example `
          .\docs\comparison\03.develop\easyobs\setup\ec2\terraform\single\terraform.tfvars
```

```bash
cp docs/comparison/03.develop/easyobs/setup/ec2/terraform/single/terraform.tfvars.example \
   docs/comparison/03.develop/easyobs/setup/ec2/terraform/single/terraform.tfvars
```

`terraform.tfvars` 의 핵심 키: `aws_region`, `instance_type`, `allow_easyobs_cidr`,
`enable_data_volume`, `easyobs_api_image_tag`, `easyobs_web_image_tag`,
`seed_mock_data`. `terraform.tfvars` 는 `.gitignore` 의해 커밋되지 않습니다.
Postgres 비번/JWT 시크릿은 Terraform 이 자동 생성합니다.

### 1-2. 적용 (apply)

**Windows (PowerShell)** — 루트에서:

```powershell
.\docs\comparison\03.develop\easyobs\setup\ec2\terraform\single\tf-apply.ps1
```

**macOS / Linux / Git Bash** — 루트에서:

```bash
bash ./docs/comparison/03.develop/easyobs/setup/ec2/terraform/single/tf-apply.sh
```

스크립트는 `terraform init` → `validate` → `plan -out tfplan` 후, 확인에
`y` 를 입력하면 `terraform apply tfplan` 을 실행합니다.

### 1-3. 출력값 확인

```powershell
cd .\docs\comparison\03.develop\easyobs\setup\ec2\terraform\single
terraform output
terraform output -raw postgres_password
terraform output -raw jwt_secret
```

| Output | 의미 |
|--------|------|
| `easyobs_api_url` | API 베이스 URL (`/docs`, `/healthz`, `/v1/...`) |
| `easyobs_web_url` | Next.js 콘솔 URL |
| `public_ip`       | EIP |
| `stage_bucket`    | 소스/product zip 이 올라간 S3 (디버깅용) |

bootstrap 과정은 `/var/log/easyobs-bootstrap.log` 와 SSM Session Manager
로 확인 가능. 약 5~10분 후 `easyobs-api` 컨테이너가 healthy 상태가 되면
콘솔 접속 가능.

> 첫 가입자는 자동 슈퍼 어드민으로 등록됩니다 (`administrator` 조직).
> `seed_mock_data=true` 로 두면 첫 부팅에 데모 트레이스가 시드되지만
> 카탈로그가 비어 있어야만 동작하므로 운영 데이터를 덮을 수 없습니다.

### 1-4. 삭제

**Windows (PowerShell)**:

```powershell
.\docs\comparison\03.develop\easyobs\setup\ec2\terraform\single\tf-delete.ps1
```

**macOS / Linux / Git Bash**:

```bash
bash ./docs/comparison/03.develop/easyobs/setup/ec2/terraform/single/tf-delete.sh
```

---

## 2. 클러스터 (cluster) — Worker 수평 확장

ALB + 다중 EC2(`leader 1`, `worker N` ASG) + 단독 Web EC2 + RDS Postgres +
EFS (블롭 공유) 구성. 트래픽이 늘면 `api_worker_desired_capacity` 또는 ASG
콘솔에서 EC2 수만 늘리면 됩니다.

```
                                  ┌─ Web EC2 (Next.js, 3000)
   Internet → ALB :80 ─┬─ /  ─────┘
                       │
                       └─ /v1/* /otlp/* /healthz /docs /openapi.json
                                ─→ API target group
                                     ├─ Leader EC2 (alarm=true)
                                     └─ Worker ASG (alarm=false, N대)
                                          │
                                          └─ RDS Postgres + EFS (blob)
```

### 2-1. 변수 설정

```bash
cp docs/comparison/03.develop/easyobs/setup/ec2/terraform/cluster/terraform.tfvars.example \
   docs/comparison/03.develop/easyobs/setup/ec2/terraform/cluster/terraform.tfvars
```

핵심 키:

| 변수 | 기본 | 설명 |
|------|------|------|
| `instance_type`              | `t3.medium` | 모든 EC2 (leader/worker/web) 동일 클래스 |
| `api_worker_min_size`        | `1` | ASG 최소 |
| `api_worker_desired_capacity`| `2` | 시작 시 띄울 워커 수 |
| `api_worker_max_size`        | `6` | 자동 확장 상한 |
| `rds_instance_class`         | `db.t3.medium` | RDS Postgres |
| `rds_allocated_storage`      | `50` GB | RDS 디스크 |
| `rds_multi_az`               | `false` | dev 기본 false. 운영은 true 권장 |
| `rds_skip_final_snapshot`    | `true` | dev 기본 true. 운영은 false |
| `allow_alb_cidr`             | `0.0.0.0/0` | ALB ingress 화이트리스트 |

### 2-2. 적용

**Windows (PowerShell)**:

```powershell
.\docs\comparison\03.develop\easyobs\setup\ec2\terraform\cluster\tf-apply.ps1
```

**macOS / Linux / Git Bash**:

```bash
bash ./docs/comparison/03.develop/easyobs/setup/ec2/terraform/cluster/tf-apply.sh
```

> 클러스터는 RDS · NAT Gateway · ASG · ALB 가 모두 만들어지는 데
> **15~20분** 정도 걸립니다.

### 2-3. 출력값 확인

```bash
cd docs/comparison/03.develop/easyobs/setup/ec2/terraform/cluster
terraform output
terraform output -raw rds_password
terraform output -raw jwt_secret
```

| Output | 의미 |
|--------|------|
| `easyobs_url`            | 진입점 (ALB DNS, HTTP) |
| `alb_dns_name`           | ALB DNS 그 자체 (Route53 CNAME 연결 등에 사용) |
| `api_leader_instance_id` | alarm 활성화된 유일한 EC2 |
| `api_worker_asg_name`    | 워커 ASG. `desired_capacity` 변경으로 즉시 확장 |
| `rds_endpoint`           | RDS Postgres 엔드포인트 |
| `efs_id`                 | 블롭 공유 EFS |

### 2-4. 워커 늘리기 / 줄이기

```bash
aws autoscaling set-desired-capacity \
  --auto-scaling-group-name "$(terraform output -raw api_worker_asg_name)" \
  --desired-capacity 4
```

또는 `terraform.tfvars` 의 `api_worker_desired_capacity` 를 바꾸고 다시 apply.
새 워커는 부팅 후 user_data 가 끝나면 ALB target group 에 자동 등록됩니다.

### 2-5. 삭제

```powershell
.\docs\comparison\03.develop\easyobs\setup\ec2\terraform\cluster\tf-delete.ps1
```

```bash
bash ./docs/comparison/03.develop/easyobs/setup/ec2/terraform/cluster/tf-delete.sh
```

> RDS `skip_final_snapshot=false` 로 둔 운영 환경은 destroy 시 final
> snapshot 이 생성됩니다. 잊지 말고 비용/보존정책 정리.

---

## 3. 동작 점검 (apply 직후)

```bash
# single
EASYOBS_URL=$(terraform -chdir=docs/comparison/03.develop/easyobs/setup/ec2/terraform/single output -raw easyobs_api_url)
curl -fsS "$EASYOBS_URL/healthz"

# cluster
EASYOBS_URL=$(terraform -chdir=docs/comparison/03.develop/easyobs/setup/ec2/terraform/cluster output -raw easyobs_url)
curl -fsS "$EASYOBS_URL/healthz"
```

OTLP/HTTP ingest 동작 확인 (빈 본문이면 400 이지만 라우터가 살아 있다는 의미):

```bash
curl -i -X POST -H "Content-Type: application/x-protobuf" \
  "$EASYOBS_URL/otlp/v1/traces" --data-binary ""
```

## 4. 폐쇄망(오프라인) 배포

EC2 Terraform 은 인터넷이 되는 환경을 전제로 합니다. **폐쇄망은 별도의
Docker 이미지 tar 번들 + 배포 스크립트** 흐름을 사용합니다. 자세한 절차는
다음을 참고:

- 빌드 스크립트: `docs/comparison/03.develop/easyobs/setup/offline/build-bundle.sh`
  / `build-bundle.ps1`
- 가이드 문서:   `docs/comparison/03.develop/easyobs/setup/offline/README.md`

요약:

```bash
# 1) 인터넷 가능 빌드 머신
./docs/comparison/03.develop/easyobs/setup/offline/build-bundle.sh --output ./dist/easyobs-bundle

# 2) ./dist/easyobs-bundle 을 매체로 폐쇄망에 옮긴 뒤
cd /path/to/easyobs-bundle
./load-bundle.sh
./deploy-single.sh                   # 또는
./deploy-cluster.sh single-host 4    # 단일 호스트에 4 워커
./deploy-cluster.sh multi-host worker  # 다중 호스트, 역할별
```

폐쇄망 클러스터의 워커 노드 확장은 docker run 단위로 호스트마다 컨테이너
1개씩 띄우는 형태로, NFS/EFS 공유 + 외부 Postgres + 사내 LB 가 전제됩니다.
스크립트가 `EASYOBS_DATABASE_URL`, `EASYOBS_JWT_SECRET`,
`EASYOBS_BLOB_HOST_DIR` 같은 외부 입력만 받으면 됩니다.

---

## 5. 인프라 삭제 (destroy) 공통 주의사항

이 스택으로 만든 AWS 리소스를 제거할 때는 **반드시 Terraform 으로 destroy**
하는 것이 안전합니다. 콘솔에서만 EC2 를 지우면 VPC/EIP/ALB/ASG/EFS 등이
남을 수 있습니다.

- **state 와 실제 리소스**: `terraform destroy` 는 **현재 디렉터리의
  `terraform.tfstate` 에 남아 있는 리소스** 를 기준으로 삭제합니다. state
  파일을 잃었거나 다른 곳에서 apply 했다면 destroy 만으로 정리가 끝나지
  않을 수 있으며, 이 경우 AWS 콘솔에서 남은 리소스를 수동으로 정리해야
  합니다.
- **S3 stage bucket**: Terraform 이 만든 S3 버킷은 `force_destroy=true` 라
  destroy 시 모든 object/version 이 함께 삭제됩니다.
- **EFS / RDS**: 클러스터의 EFS 와 RDS 안에 운영 데이터가 들어 있다면
  destroy 전에 백업하세요. RDS 는 `rds_skip_final_snapshot=false` 면
  자동 final snapshot 이 만들어집니다.
- **계획만 보기**: 무엇이 삭제되는지 미리 보려면 해당 디렉터리에서
  `terraform plan -destroy`.

---

## 관련 경로

| 항목 | 경로 |
|------|------|
| EasyObs 소스 (개발용) | `docs/comparison/03.develop/easyobs/` |
| Dockerfile (API)      | `docs/comparison/03.develop/easyobs/setup/images/api/Dockerfile` |
| Dockerfile (Web)      | `docs/comparison/03.develop/easyobs/setup/images/web/Dockerfile` |
| Compose (단일/클러스터) | `docs/comparison/03.develop/easyobs/setup/compose/` |
| Terraform (single)    | `docs/comparison/03.develop/easyobs/setup/ec2/terraform/single/` |
| Terraform (cluster)   | `docs/comparison/03.develop/easyobs/setup/ec2/terraform/cluster/` |
| 오프라인 번들 도구     | `docs/comparison/03.develop/easyobs/setup/offline/` |
| 종합 README           | `docs/comparison/03.develop/easyobs/setup/README.md` |
