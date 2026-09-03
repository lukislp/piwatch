## [1.39.4](https://github.com/lukislp/piwatch/compare/v1.39.3...v1.39.4) (2026-09-03)


### Bug Fixes

* **ci:** add Dependabot for github-actions, npm, pip, docker ([45bf9de](https://github.com/lukislp/piwatch/commit/45bf9de175b2d23e92660438837a158ef5c23ae8))

## [1.39.3](https://github.com/lukislp/piwatch/compare/v1.39.2...v1.39.3) (2026-08-24)


### Performance Improvements

* **ci:** native per-arch docker builds instead of QEMU emulation ([dd5f74e](https://github.com/lukislp/piwatch/commit/dd5f74e8760fc9e1ac505f21f51f0b342a7db1e1))

## [1.39.2](https://github.com/lukislp/piwatch/compare/v1.39.1...v1.39.2) (2026-08-24)


### Bug Fixes

* **deploy:** acknowledge the node-agent's accepted privileged finding ([c7d3583](https://github.com/lukislp/piwatch/commit/c7d35836821bec0e2d00d9cd2b9a60673c05f84d))

## [1.39.1](https://github.com/lukislp/piwatch/compare/v1.39.0...v1.39.1) (2026-08-24)


### Bug Fixes

* run the backend as non-root ([408f2fd](https://github.com/lukislp/piwatch/commit/408f2fd1ed879db93edfa792ff1556d1d94ebc6b))

# [1.39.0](https://github.com/lukislp/piwatch/compare/v1.38.0...v1.39.0) (2026-08-22)


### Features

* own this repo's Flux GitOps wiring ([f8c9503](https://github.com/lukislp/piwatch/commit/f8c9503e202d12c27ae6d49e30861e21fc5a4070))

# [1.38.0](https://github.com/lukislp/piwatch/compare/v1.37.1...v1.38.0) (2026-08-20)


### Features

* **ml:** S4 rolling-origin cross-validation ([376e08c](https://github.com/lukislp/piwatch/commit/376e08c98ad159d8003c757bac0ba1d072212e70))

## [1.37.1](https://github.com/lukislp/piwatch/compare/v1.37.0...v1.37.1) (2026-08-20)


### Bug Fixes

* **ml:** stop MLflow from leaking a stray top-level mlruns/ directory ([7c10706](https://github.com/lukislp/piwatch/commit/7c10706a3eae10b3508d0036c529084ecb01042c))

# [1.37.0](https://github.com/lukislp/piwatch/compare/v1.36.0...v1.37.0) (2026-08-17)


### Features

* **ml:** S4 fair model-vs-baseline evaluation ([98f5605](https://github.com/lukislp/piwatch/commit/98f5605e230ab428ccbb7fc0e57aab467a41026f))
* **ml:** S5 Telegram alerting (standalone, independent of the model) ([b8d37b4](https://github.com/lukislp/piwatch/commit/b8d37b47faa9b3060f6c4e00b745b83f746f3319))

# [1.36.0](https://github.com/lukislp/piwatch/compare/v1.35.0...v1.36.0) (2026-08-17)


### Features

* **ml:** S3 MLflow experiment tracking ([c9b46eb](https://github.com/lukislp/piwatch/commit/c9b46ebe476f691add62aa3d2ce628d8925ae681))

# [1.35.0](https://github.com/lukislp/piwatch/compare/v1.34.0...v1.35.0) (2026-08-17)


### Features

* **ml:** S2 Isolation Forest baseline (multivariate) ([649f098](https://github.com/lukislp/piwatch/commit/649f098b7e838aaa4b4f36da3702c88ab4a2ae7a))
* **ml:** S3 LSTM autoencoder + training pipeline ([ddff626](https://github.com/lukislp/piwatch/commit/ddff62644b092fd7f9ae4ef23bfb58d8666a8b44))

# [1.34.0](https://github.com/lukislp/piwatch/compare/v1.33.0...v1.34.0) (2026-08-17)


### Features

* **ml:** S2 statistical baselines (Z-score, EWMA) + shared eval harness ([50be890](https://github.com/lukislp/piwatch/commit/50be890567c03c5797e482dd52d9242ae2eaecb5))

# [1.33.0](https://github.com/lukislp/piwatch/compare/v1.32.0...v1.33.0) (2026-08-17)


### Features

* **ml:** S1 exploration + injected anomaly types ([aa37692](https://github.com/lukislp/piwatch/commit/aa37692a3aefec9f32fc79f5299475044c60420b))

# [1.32.0](https://github.com/lukislp/piwatch/compare/v1.31.1...v1.32.0) (2026-08-16)


### Features

* **ml:** add S1 data export tooling for anomaly detection project ([e3e7513](https://github.com/lukislp/piwatch/commit/e3e7513a74e4497f088df1e961ba15bb2a4f16fe))

## [1.31.1](https://github.com/lukislp/piwatch/compare/v1.31.0...v1.31.1) (2026-08-16)


### Bug Fixes

* grant RBAC read access for Secrets/ConfigMaps ([a535452](https://github.com/lukislp/piwatch/commit/a5354522509f9ce64ce58fae783e5381c62a63d4)), closes [#40](https://github.com/lukislp/piwatch/issues/40)

# [1.31.0](https://github.com/lukislp/piwatch/compare/v1.30.0...v1.31.0) (2026-08-16)


### Features

* show Secret/ConfigMap age on the Workloads page ([318443a](https://github.com/lukislp/piwatch/commit/318443a753bac35485def45e23c26005104ef4b6))

# [1.30.0](https://github.com/lukislp/piwatch/compare/v1.29.0...v1.30.0) (2026-08-16)


### Bug Fixes

* re-trigger CI after semantic-release skipped again for the duplicate-hostname-detection PR ([7862321](https://github.com/lukislp/piwatch/commit/7862321011c454142933d171d8bf8a535a1b971d))
* re-trigger CI after semantic-release skipped for the duplicate-hostname-detection PR ([4521211](https://github.com/lukislp/piwatch/commit/4521211a6b161126b9ca8ea1cfede436fe4b917d))


### Features

* flag HTTPRoutes that claim the same hostname ([148c77d](https://github.com/lukislp/piwatch/commit/148c77d404344eaf9c64375996bc847497b45e6e))

# [1.29.0](https://github.com/lukislp/piwatch/compare/v1.28.0...v1.29.0) (2026-08-16)


### Features

* add per-namespace summary card to the Workloads page ([7cfc3ea](https://github.com/lukislp/piwatch/commit/7cfc3ea9f386bd5c3d52bfc273bc2fdf92f4f92c))

# [1.28.0](https://github.com/lukislp/piwatch/compare/v1.27.0...v1.28.0) (2026-08-16)


### Features

* show pod placement (bin-packing) on the Nodes page ([9002c0c](https://github.com/lukislp/piwatch/commit/9002c0c8ae23bdbcf724092e8e4b0d307d410200))

# [1.27.0](https://github.com/lukislp/piwatch/compare/v1.26.0...v1.27.0) (2026-08-16)


### Features

* add healthcheck CSV export for ad-hoc SLA reports ([9d19d26](https://github.com/lukislp/piwatch/commit/9d19d265d67f1c1d559178635eaa17e1e26a290d))

# [1.26.0](https://github.com/lukislp/piwatch/compare/v1.25.0...v1.26.0) (2026-08-16)


### Features

* show init container failure detail on the Workloads pods table ([80b7fac](https://github.com/lukislp/piwatch/commit/80b7fac0cb834ecfdc8c735d7c5f5e2ea912e76c))

# [1.25.0](https://github.com/lukislp/piwatch/compare/v1.24.0...v1.25.0) (2026-08-16)


### Features

* always-on CoreDNS resolution healthcheck ([a7a1b83](https://github.com/lukislp/piwatch/commit/a7a1b833a08fcc71baa0c28a4df6666d89b8978b))

# [1.24.0](https://github.com/lukislp/piwatch/compare/v1.23.0...v1.24.0) (2026-08-16)


### Features

* split networking cards into a dedicated Network tab ([f0f75ff](https://github.com/lukislp/piwatch/commit/f0f75fff8b79e3a5640fae0a0de4e2c2c9ba3b2b))

# [1.23.0](https://github.com/lukislp/piwatch/compare/v1.22.0...v1.23.0) (2026-08-16)


### Features

* show RateLimitPolicy overview on the Overview page ([20657df](https://github.com/lukislp/piwatch/commit/20657df0191c186caf1a2f1a4c70911a0ead1764))

# [1.22.0](https://github.com/lukislp/piwatch/compare/v1.21.1...v1.22.0) (2026-08-16)


### Bug Fixes

* re-trigger CI after semantic-release skipped for the NetworkPolicy PR ([28d97bc](https://github.com/lukislp/piwatch/commit/28d97bc4086a52b06956a36a51e3b7ddb5a0790a))


### Features

* show NetworkPolicy overview on the Overview page ([b3a8504](https://github.com/lukislp/piwatch/commit/b3a850471484edc4121c3080034253d16fcf4819))

## [1.21.1](https://github.com/lukislp/piwatch/compare/v1.21.0...v1.21.1) (2026-08-15)


### Bug Fixes

* HPA scaling-limited false positive when capped at the floor ([50b0e92](https://github.com/lukislp/piwatch/commit/50b0e92c6657e9a99a3b9ac42b509115724300da))

# [1.21.0](https://github.com/lukislp/piwatch/compare/v1.20.0...v1.21.0) (2026-08-15)


### Features

* show node cordon/taint status on the Overview page ([57628d7](https://github.com/lukislp/piwatch/commit/57628d7605227c1a3b354d2dd0127187da0c97da))

# [1.20.0](https://github.com/lukislp/piwatch/compare/v1.19.0...v1.20.0) (2026-08-15)


### Features

* persist node history to survive pod restarts, bounded retention ([10d583f](https://github.com/lukislp/piwatch/commit/10d583f8f9136fdc935c59f35e8732558ec5bd8c))

# [1.19.0](https://github.com/lukislp/piwatch/compare/v1.18.3...v1.19.0) (2026-08-15)


### Features

* show HorizontalPodAutoscaler status on the Workloads page ([e44e368](https://github.com/lukislp/piwatch/commit/e44e368382ff9380068a6892ca56885864a77713))

## [1.18.3](https://github.com/lukislp/piwatch/compare/v1.18.2...v1.18.3) (2026-08-15)


### Bug Fixes

* balance Overview tile grid across rows instead of a lone remainder ([b3eda1c](https://github.com/lukislp/piwatch/commit/b3eda1c73cc0d20213d47c04fea459d43cc10ddd))
* re-trigger CI after semantic-release skipped due to a mid-run push race ([ce8dfde](https://github.com/lukislp/piwatch/commit/ce8dfde520c41bd7e020a080dc229fb7154acb7f))

## [1.18.2](https://github.com/lukislp/piwatch/compare/v1.18.1...v1.18.2) (2026-08-15)


### Bug Fixes

* auto-healthcheck 4xx false positives and missing config on first WS delta ([af36ef5](https://github.com/lukislp/piwatch/commit/af36ef55f9f9ddabc9e21524eb18b676ee2a93f5))

## [1.18.1](https://github.com/lukislp/piwatch/compare/v1.18.0...v1.18.1) (2026-08-15)


### Bug Fixes

* keep the Logs tab pod select within its container on mobile ([d8471fd](https://github.com/lukislp/piwatch/commit/d8471fd34c4b80270448acb9821e70ea1a8a4cb5))

# [1.18.0](https://github.com/lukislp/piwatch/compare/v1.17.1...v1.18.0) (2026-08-15)


### Features

* auto-generate healthchecks from discovered routes/Services ([ee33dcf](https://github.com/lukislp/piwatch/commit/ee33dcf527623e316a48ce2ff174aab833d2f71b))

## [1.17.1](https://github.com/lukislp/piwatch/compare/v1.17.0...v1.17.1) (2026-08-15)


### Bug Fixes

* lone last-row Overview tile stretching, table overflow on mobile ([4b2b44b](https://github.com/lukislp/piwatch/commit/4b2b44baa6f73ea1dfee6d55a73d89269063e8f7))

# [1.17.0](https://github.com/lukislp/piwatch/compare/v1.16.0...v1.17.0) (2026-08-15)


### Features

* detect orphaned PersistentVolumes on the Workloads page ([319528b](https://github.com/lukislp/piwatch/commit/319528b05182317818a0356cb1712be3800f87da))

# [1.16.0](https://github.com/lukislp/piwatch/compare/v1.15.0...v1.16.0) (2026-08-15)


### Features

* show LoadBalancer Service status on the Overview page ([19e83f6](https://github.com/lukislp/piwatch/commit/19e83f6f0dd0a7bde37d4666bbf272a9ab412577))

# [1.15.0](https://github.com/lukislp/piwatch/compare/v1.14.0...v1.15.0) (2026-08-15)


### Features

* show node pressure conditions and pod restart reason/exit code ([6507d05](https://github.com/lukislp/piwatch/commit/6507d055cec13292c650bd6d9faf7f16a8eac9c2))

# [1.14.0](https://github.com/lukislp/piwatch/compare/v1.13.0...v1.14.0) (2026-08-15)


### Features

* show Gateway API routing status on the Overview page ([0c30cf7](https://github.com/lukislp/piwatch/commit/0c30cf7e162162dccb8e9ce5fc58f09915c8b17b))

# [1.13.0](https://github.com/lukislp/piwatch/compare/v1.12.0...v1.13.0) (2026-08-15)


### Features

* watch StatefulSets and DaemonSets on the Workloads page ([10877d3](https://github.com/lukislp/piwatch/commit/10877d3f2e03fc8543d47c8081a325cbf50749cc))

# [1.12.0](https://github.com/lukislp/piwatch/compare/v1.11.2...v1.12.0) (2026-08-15)


### Features

* show the currently installed tag in the Image Automation card ([92f624d](https://github.com/lukislp/piwatch/commit/92f624d3a5dbdced9b16e9aab05c07874af51789))

## [1.11.2](https://github.com/lukislp/piwatch/compare/v1.11.1...v1.11.2) (2026-08-15)


### Bug Fixes

* discard PVC usage % when Prometheus reports the node disk, not the volume ([e0e8e35](https://github.com/lukislp/piwatch/commit/e0e8e35b56bc7db8e9b160e67768293db0f4b2e4))

## [1.11.1](https://github.com/lukislp/piwatch/compare/v1.11.0...v1.11.1) (2026-08-15)


### Bug Fixes

* enable PVC usage % via PIWATCH_PROMETHEUS_URL ([7ca3849](https://github.com/lukislp/piwatch/commit/7ca38496e96536e6392717769ea852eb39dff934))

# [1.11.0](https://github.com/lukislp/piwatch/compare/v1.10.0...v1.11.0) (2026-08-15)


### Features

* show cluster-wide CPU/RAM capacity on the Overview page ([e5d9d91](https://github.com/lukislp/piwatch/commit/e5d9d915a4cf721a9d5d58f95fb414c5ac5f04a8))

# [1.10.0](https://github.com/lukislp/piwatch/compare/v1.9.0...v1.10.0) (2026-08-15)


### Features

* show PVC storage usage on the Workloads page ([5565315](https://github.com/lukislp/piwatch/commit/55653152c7a6bb3c2c36a7366cea7538297c447b))

# [1.9.0](https://github.com/lukislp/piwatch/compare/v1.8.0...v1.9.0) (2026-08-15)


### Features

* extend Flux GitOps card with image automation, resource count, and source status ([2f214c2](https://github.com/lukislp/piwatch/commit/2f214c29ca9b56fbae317d426fb1fd9d87619600))

# [1.8.0](https://github.com/lukislp/piwatch/compare/v1.7.1...v1.8.0) (2026-08-15)


### Features

* show a live countdown to the next Flux reconcile ([0f92cff](https://github.com/lukislp/piwatch/commit/0f92cff1992a2c1905cb0947cccb446fa1751b52))

## [1.7.1](https://github.com/lukislp/piwatch/compare/v1.7.0...v1.7.1) (2026-08-15)


### Bug Fixes

* rollout-drift false positive for sibling deployments sharing a name prefix ([6a5f56a](https://github.com/lukislp/piwatch/commit/6a5f56adc83150d90bfb8963c68914ce6a88a009))

# [1.7.0](https://github.com/lukislp/piwatch/compare/v1.6.0...v1.7.0) (2026-08-15)


### Features

* detect OOMKilled containers in the Workloads table ([0011a46](https://github.com/lukislp/piwatch/commit/0011a46976f2e4aac0355d0b0825de234958f99a))

# [1.6.0](https://github.com/lukislp/piwatch/compare/v1.5.1...v1.6.0) (2026-08-15)


### Features

* detect Deployment rollout drift ([7817783](https://github.com/lukislp/piwatch/commit/7817783bf0b776ca257b5abf31d18f13cc009072))
* show Flux Kustomization GitOps sync status ([10c3aab](https://github.com/lukislp/piwatch/commit/10c3aab0d92e14530de4a93bdb82835067aafbb3))
* show per-node network throughput ([c82c80d](https://github.com/lukislp/piwatch/commit/c82c80d114a45f450ab98144b5f5ae97edf073cb))

## [1.5.1](https://github.com/lukislp/piwatch/compare/v1.5.0...v1.5.1) (2026-08-15)


### Bug Fixes

* live-update the full NVMe/hardware payload, not just a whitelist ([38e27cb](https://github.com/lukislp/piwatch/commit/38e27cbda03d2677161b0ee2e406800f6919d0be))

# [1.5.0](https://github.com/lukislp/piwatch/compare/v1.4.1...v1.5.0) (2026-08-15)


### Features

* add a dedicated NVMe monitoring tab ([943c8be](https://github.com/lukislp/piwatch/commit/943c8be1c83b91790562d0346ec7a7ae96733a7a))

## [1.4.1](https://github.com/lukislp/piwatch/compare/v1.4.0...v1.4.1) (2026-08-15)


### Bug Fixes

* **deploy:** explicitly allow privilege escalation for node-agent ([7cd0546](https://github.com/lukislp/piwatch/commit/7cd05466b71778135f47dd046c90d5e3b10ede28))
* wrap long table cell content instead of overflowing the page ([92aeee7](https://github.com/lukislp/piwatch/commit/92aeee7f8a3bf0eccdef5aecbcdc5397eb73cf65))

# [1.4.0](https://github.com/lukislp/piwatch/compare/v1.3.0...v1.4.0) (2026-08-15)


### Features

* **flux:** wire piwatch into Flux-managed GitOps deployment ([52b6cef](https://github.com/lukislp/piwatch/commit/52b6cef44a507af31671ab19805bb2a10cca71f8))

# [1.3.0](https://github.com/lukislp/piwatch/compare/v1.2.0...v1.3.0) (2026-08-15)


### Features

* monitor NVMe SSD health and Pi under-voltage on the PoE+ M.2 HAT ([c02f7d5](https://github.com/lukislp/piwatch/commit/c02f7d5c4991fe4333778ef2924260593f2bbdd9))

# [1.2.0](https://github.com/lukislp/piwatch/compare/v1.1.3...v1.2.0) (2026-08-15)


### Features

* show per-pod/workload CPU and RAM usage ([7fa0258](https://github.com/lukislp/piwatch/commit/7fa02581013842226ee3de8080f6c49484184572))

## [1.1.3](https://github.com/lukislp/piwatch/compare/v1.1.2...v1.1.3) (2026-08-07)


### Bug Fixes

* correct README deployment section to match the real CI/CD-built image ([eb4f13e](https://github.com/lukislp/piwatch/commit/eb4f13e8b629850b54b9335bc539f2fe6e64372c))

## [1.1.2](https://github.com/lukislp/piwatch/compare/v1.1.1...v1.1.2) (2026-08-07)


### Bug Fixes

* re-trigger CI after the previous push's webhook was dropped during a GitHub Actions incident ([fa9b19c](https://github.com/lukislp/piwatch/commit/fa9b19c35cc26332b079a7381283e67eb820f21b))

## [1.1.1](https://github.com/lukislp/piwatch/compare/v1.1.0...v1.1.1) (2026-08-06)


### Bug Fixes

* catch IndexError in read_uptime_s and read_meminfo like read_load already does ([5f29d23](https://github.com/lukislp/piwatch/commit/5f29d23b2b72b2cbb99fd5a0c0a9d5a729fcc503))
* close the server-side writer in the raw TCP test helper ([6295440](https://github.com/lukislp/piwatch/commit/62954403da7c87aa9a16116876f88e8fe4c7ee49))

# [1.1.0](https://github.com/lukislp/piwatch/compare/v1.0.3...v1.1.0) (2026-08-05)


### Bug Fixes

* remove invalid working-directory key on the upload-artifact step ([fee884f](https://github.com/lukislp/piwatch/commit/fee884f4eec76bd67776c78715e328730d28dcf1))


### Features

* add a self-hosted test coverage badge ([c5822f0](https://github.com/lukislp/piwatch/commit/c5822f08ad54fa2d540f787927e867b11d45e08b))

## [1.0.3](https://github.com/lukislp/piwatch/compare/v1.0.2...v1.0.3) (2026-08-05)


### Bug Fixes

* add an overview dashboard screenshot to the README ([97d2c7a](https://github.com/lukislp/piwatch/commit/97d2c7adfdc20c87c080b52712649bda6879ecdf))

## [1.0.2](https://github.com/lukislp/piwatch/compare/v1.0.1...v1.0.2) (2026-08-05)


### Bug Fixes

* surface the public live demo link in the README ([9d5e61c](https://github.com/lukislp/piwatch/commit/9d5e61c0b71d55976093915d711764acc0fe6fd3))

## [1.0.1](https://github.com/lukislp/piwatch/compare/v1.0.0...v1.0.1) (2026-08-05)


### Bug Fixes

* surface build/release/license status via README badges ([7c8d85d](https://github.com/lukislp/piwatch/commit/7c8d85d8252ceef622ecf823960abefb8f460fea))

# 1.0.0 (2026-08-04)


### Bug Fixes

* resolve real lint/audit findings surfaced by the new CI checks ([c2eb27a](https://github.com/lukislp/piwatch/commit/c2eb27a9404381a91b373d75d006d3adcbb28605))


### Features

* add GitHub Actions CI/CD pipeline ([bfde393](https://github.com/lukislp/piwatch/commit/bfde39339254d4ea86933d8db3c34303491090bb))
