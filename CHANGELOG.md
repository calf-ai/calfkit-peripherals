# Changelog

## 0.1.0 (2026-06-13)


### ⚠ BREAKING CHANGES

* unify vendored tools into a single calfkit-tools distribution ([#5](https://github.com/calf-ai/calfkit-peripherals/issues/5))

### Features

* **node:** all 10 tool nodes — web_fetch, shell, files, todo, web (TDD) ([c413dc1](https://github.com/calf-ai/calfkit-peripherals/commit/c413dc16c3ee5008982bb94b7b15e9905b91753d))
* **node:** execute_code node + Stage D docs (NODE.md ×2, READMEs) ([3ccdca7](https://github.com/calf-ai/calfkit-peripherals/commit/3ccdca7ec9894b1d3496f6dbccbe97625b8f77c3))
* **node:** Stage D groundwork — ADR-0004 tenancy design, calfkit dep, hermes node runtime seam ([7b26a11](https://github.com/calf-ai/calfkit-peripherals/commit/7b26a116a7dc514600cd8ea779aeabbec51bb897))


### Bug Fixes

* **node:** Stage D review-fix wave — converged per-tool deep reviews ([#4](https://github.com/calf-ai/calfkit-peripherals/issues/4)) ([48909f0](https://github.com/calf-ai/calfkit-peripherals/commit/48909f0f757b794b90294a5022e913334833e1d1))
* web-tools vendor: deep-review fixes (gzip bug + SSRF credential-strip tests) ([#2](https://github.com/calf-ai/calfkit-peripherals/issues/2)) ([177a7e3](https://github.com/calf-ai/calfkit-peripherals/commit/177a7e38a8dc9863562799f73a777092e587820b))


### Documentation

* **notices:** index hermes-agent + pydantic-web-fetch vendored components ([b6880bf](https://github.com/calf-ai/calfkit-peripherals/commit/b6880bf2e25aa04cf52219550f691877d84e8198))
* update README ([6fa9288](https://github.com/calf-ai/calfkit-peripherals/commit/6fa9288aa15a2545355ea63a22a84cb85d5c52f2))
* update README ([06a6009](https://github.com/calf-ai/calfkit-peripherals/commit/06a60098e62b8ad71650d7335a21a22e2b542ec2))
* **web-tools:** amend per deep-review round 2 (6 agents, 3 per doc) ([e215362](https://github.com/calf-ai/calfkit-peripherals/commit/e215362c60c6be271da5170207588b6eb2f04a81))
* **web-tools:** amend spec per deep-review round 1 ([614e114](https://github.com/calf-ai/calfkit-peripherals/commit/614e114eafae79feb1928ec8a7ad4203737dbb3c))
* **web-tools:** design + ADRs for web_fetch + web_search vendoring ([f2e4649](https://github.com/calf-ai/calfkit-peripherals/commit/f2e4649dfa98f0174eaf4f2432bac56cba2a4d4f))
* **web-tools:** drop web_tools.py; node returns raw provider content ([ad0c9d9](https://github.com/calf-ai/calfkit-peripherals/commit/ad0c9d9e0bc4e547f060848804321cff04d197d9))
* **web-tools:** resolve M1/M2 simplifications ([a7d489e](https://github.com/calf-ai/calfkit-peripherals/commit/a7d489e2927e03a0b821812a63de1843ba0e1faf))
* **web-tools:** split into per-tool specs + plans ([c68c353](https://github.com/calf-ai/calfkit-peripherals/commit/c68c35395b8f0efbc75fa357ead7a21516600ab1))


### Code Refactoring

* unify vendored tools into a single calfkit-tools distribution ([#5](https://github.com/calf-ai/calfkit-peripherals/issues/5)) ([451c532](https://github.com/calf-ai/calfkit-peripherals/commit/451c532d41274927685bf6f46f32a34175e07bf2))
