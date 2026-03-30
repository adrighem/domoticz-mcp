# Changelog

## [1.0.0](https://github.com/adrighem/domoticz-mcp/compare/v0.2.0...v1.0.0) (2026-03-30)


### Features

* release version 1.0.0 with performance optimizations and diagnostic tools ([aa44988](https://github.com/adrighem/domoticz-mcp/commit/aa4498827db2636b251ae81d341c2f9ada2e8b9a))


### Bug Fixes

* Correct types ([203ced4](https://github.com/adrighem/domoticz-mcp/commit/203ced48374a84c37ea1f1f2dfd9216d3a459c6f))


### Miscellaneous Chores

* release 1.0.0 ([42d5fb5](https://github.com/adrighem/domoticz-mcp/commit/42d5fb5161ad4d91a9ccad4856540b5742a207fc))

## [0.2.0](https://github.com/adrighem/domoticz-mcp/compare/v0.1.4...v0.2.0) (2026-03-26)


### Features

* add environment variable support for all settings ([#8](https://github.com/adrighem/domoticz-mcp/issues/8)) ([e06dafe](https://github.com/adrighem/domoticz-mcp/commit/e06dafe1b0998584cf74b7fc444b4e12daf55fd4))

## [0.1.4](https://github.com/adrighem/domoticz-mcp/compare/v0.1.3...v0.1.4) (2026-03-24)


### Bug Fixes

* strip quotes from environment variables ([#3](https://github.com/adrighem/domoticz-mcp/issues/3)) ([5cffe05](https://github.com/adrighem/domoticz-mcp/commit/5cffe0552cca7fb91bf6c7dc59732485fd5a042b))

## [0.1.3](https://github.com/adrighem/domoticz-mcp/compare/v0.1.2...v0.1.3) (2026-03-24)


### Bug Fixes

* ensure DOMOTICZ_URL has a valid http/https prefix ([b09aef5](https://github.com/adrighem/domoticz-mcp/commit/b09aef55ce3141d726b316ff6d1db6b39d322867))


### Documentation

* add project logo ([e2641e4](https://github.com/adrighem/domoticz-mcp/commit/e2641e464a73bbeaf56506e3d4684adea2175e7f))

## [0.1.2](https://github.com/adrighem/domoticz-mcp/compare/v0.1.1...v0.1.2) (2026-03-23)


### Bug Fixes

* disable FastMCP DNS rebinding protection to allow LAN connections ([7bab344](https://github.com/adrighem/domoticz-mcp/commit/7bab344c1de614411ab5e297fa744f1cbc85e0f5))


### Documentation

* add project logic for issue management ([9b7e9c7](https://github.com/adrighem/domoticz-mcp/commit/9b7e9c7fd24a14d8f4ee765506ed625146f3798b))

## [0.1.1](https://github.com/adrighem/domoticz-mcp/compare/v0.1.0...v0.1.1) (2026-03-23)


### Bug Fixes

* Add CORS middleware and streamable-http transport for WebUI clients ([#3](https://github.com/adrighem/domoticz-mcp/issues/3)) ([d3279a7](https://github.com/adrighem/domoticz-mcp/commit/d3279a7db8a7bbf5af05345859e5f65c8d46f484))


### Documentation

* Document HTTP transport options and connection URLs ([94cd0df](https://github.com/adrighem/domoticz-mcp/commit/94cd0df2e59b85aee864505427f9967486ad5a10))

## 0.1.0 (2026-03-23)


### Features

* Add dynamic descriptive URI template for device resources ([26584da](https://github.com/adrighem/domoticz-mcp/commit/26584da0f7d7b186033608449e8888a18a1b56fb))
* Add endpoints for hardware, settings, sun times, cameras, floorplans, and users ([faf2c22](https://github.com/adrighem/domoticz-mcp/commit/faf2c221ddcab0e89402dce373e7c393261d263b))
* Add event management tools (get_event, create_event, update_event) ([3a7a264](https://github.com/adrighem/domoticz-mcp/commit/3a7a2643c9268b11ec020f9b1a38f8631ed1c941))
* Add HTTP interface, Docker support and GH Action for container image ([c600765](https://github.com/adrighem/domoticz-mcp/commit/c60076560105a6543871f873d84188d977409a30))
* Add HTTP SSE interface and Docker support ([#1](https://github.com/adrighem/domoticz-mcp/issues/1)) ([f607b88](https://github.com/adrighem/domoticz-mcp/commit/f607b883b2364801892b1c7da7f29651f47f29cc))
* Add MCP Prompts for guided AI workflows ([ec6dd4f](https://github.com/adrighem/domoticz-mcp/commit/ec6dd4fb35a923b9cda3920258ad5f9b23c8f217))
* add mcp resources for devices, rooms, user variables, and events ([9087275](https://github.com/adrighem/domoticz-mcp/commit/90872752e527ebd75511870c872df113d169c0d3))
* Add missing Domoticz API capabilities as tools and resources ([50c6bb0](https://github.com/adrighem/domoticz-mcp/commit/50c6bb08eb48b34fdd01259916221e3d04e544ba))
* Add name resolution for devices, scenes, and variables ([586a1af](https://github.com/adrighem/domoticz-mcp/commit/586a1af51780e311df04bd36d0b843d83dc69237))
* add oauth bearer token support ([8ad56b3](https://github.com/adrighem/domoticz-mcp/commit/8ad56b3c4313d31972e922a2f36e45edc636fb96))
* implement interactive oauth flow for dynamic client registration ([38a391e](https://github.com/adrighem/domoticz-mcp/commit/38a391e48f4b35c7f5176a1e2ee8244fd1a69e7e))
* Improve room-based device fetching and resources ([2d8ce99](https://github.com/adrighem/domoticz-mcp/commit/2d8ce9920d8fa0b8724d9b97a281b90407dbfdc9))


### Bug Fixes

* Correct endpoint commands for hardware, settings, and cameras ([e9b9f3b](https://github.com/adrighem/domoticz-mcp/commit/e9b9f3b3a46a70da5d656b2b6a1e964a9233c790))
* implement arg parsing for help and correct docs ([7a1af66](https://github.com/adrighem/domoticz-mcp/commit/7a1af66ff607fdb05cbfb8b130fed6cea9c531bc))
* use correct domoticz api parameters for event save/update ([9d30666](https://github.com/adrighem/domoticz-mcp/commit/9d30666029bbbe609858ec7663fffe1814fd4383))


### Documentation

* add Gemini CLI configuration and structure project ([45dcbb0](https://github.com/adrighem/domoticz-mcp/commit/45dcbb020a7f5238457fe01079a5bb66e8038f2d))
* Split Features section into Tools and Resources ([86c050b](https://github.com/adrighem/domoticz-mcp/commit/86c050bceb0a974e1dfdee4a4e230ba2f1de410b))
