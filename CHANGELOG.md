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
