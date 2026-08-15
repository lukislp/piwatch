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
