import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // EasyObs 콘솔은 인증/멀티테넌트 상태에 따라 동적으로 라우팅되며
  // /workspace/setup/organizations/[orgId] 같은 dynamic segment 를 사용한다.
  // 따라서 `output: "export"` (정적 SSG) 와는 호환되지 않으므로 사용하지 않는다.
  // 운영 컨테이너 빌드에서는 standalone 으로 빌드해 node_modules 를 통째로
  // 옮기지 않고 최소 산출물만 이미지에 넣는다. 로컬 dev (`next dev`) 에는
  // 영향이 없다.
  output: "standalone",
  trailingSlash: true,
  images: { unoptimized: true },
  devIndicators: false,
};

export default nextConfig;
