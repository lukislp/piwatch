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
