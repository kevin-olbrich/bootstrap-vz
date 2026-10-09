# TODO

Open work for bootstrap-vz from the code audit of October 2026. Every entry names the
files involved, the problem, a suggested fix and caveats. Facts marked "(unverified)"
could not be checked against Debian or vendor sources at the time. Line numbers refer
to commit 2cd4ad6 and can drift. Remove an entry when its fix lands, and record the fix
in `CHANGELOG.rst`.

## Decisions

- **Old Debian releases stay supported.** Never remove a release, a release-specific
  code path or an example manifest. Extend with new example manifests and tests instead.
  This rule is also in `AGENTS.md`.
- **Broken code paths are repaired, not removed.** This applies to the EC2 PV (pvgrub)
  and S3 instance-store path, the Oracle provider, and the chef, opennebula and salt
  plugins.
- **The release version is still open.** See "Release version" below.

## EC2

### Use IMDSv2 for instance metadata on the build host and in the EC2 init scripts

- **Where:** `bootstrapvz/providers/ec2/tasks/host.py:18-29`, `bootstrapvz/providers/ec2/assets/init.d/ec2-get-credentials:25,32`, `bootstrapvz/providers/ec2/assets/init.d/ec2-run-user-data:24-50`, `bootstrapvz/providers/ec2/tasks/packages.py:13`, `bootstrapvz/providers/ec2/__init__.py:94-95`, `bootstrapvz/common/tasks/initd.py:24-25`, `bootstrapvz/providers/ec2/tasks/ami.py:96-130`, `bootstrapvz/providers/ec2/README.rst:10-15`

- **Problem:** `GetInstanceMetadata` sends a GET without a token to `http://169.254.169.254/latest/dynamic/instance-identity/document`. On a build host with `HttpTokens=required`, IMDS answers 401 and `urlopen` raises `HTTPError`, so every EBS build aborts in the preparation phase. The document supplies the region, availability zone and instance ID used by `ebs.py:24,34`, `connection.py:75` and `plugins/prebootstrapped/tasks.py:39`. Inside the image, `ec2-get-credentials` and `ec2-run-user-data` also query IMDS without a token. They are installed unless the manifest uses `cloud_init` or sets `install_init_scripts: false`. On an IMDSv2-only instance, no key reaches `/root/.ssh/authorized_keys`, so the instance cannot be reached over SSH, and user-data never runs. The scripts are broken on current releases even with IMDSv1:
  - They call `wget`, which a default debootstrap does not install (priority standard, unverified). `DefaultPackages` adds only `file`.
  - `insserv -r $0` fails on systemd releases without insserv, so the "run once" user-data runs on every boot.
  - Current `file` reports gzip data as `application/gzip` (checked with file 5.45). The `application/x-gzip` comparison at line 32 therefore never matches, and gzipped user-data is skipped.

- **Fix:**
  - In `host.py`, first send `PUT http://169.254.169.254/latest/api/token` with the header `X-aws-ec2-metadata-token-ttl-seconds: 300`, then send the GET with `X-aws-ec2-metadata-token: <token>`. Fall back to a GET without a token only if the PUT fails. Cover this with a unit test that mocks `urllib.request.urlopen`.
  - In both scripts, fetch a token the same way (`curl --silent --fail --request PUT --header ...`) and send it with every request, and add `curl` to `DefaultPackages`.
  - Replace `insserv -r $0` with a per-instance marker file (for example `/var/lib/ec2-run-user-data/<instance-id>`) so the run-once logic works under both sysvinit and systemd.
  - Accept both `application/gzip` and `application/x-gzip`.
  - Once the image scripts, including `ec2net-functions` (see the secondary-ENI entry), use tokens, consider registering AMIs with `ImdsSupport='v2.0'` in `RegisterAMI`.

- **Notes:**
  - The audit claimed that bookworm's debianutils no longer ships `tempfile`. That is wrong: debianutils removed it in 5.0, restored it in 5.6, and still ships it with a deprecation warning. Switching to `mktemp` is cleanup, not a fix.
  - Every shipped EC2 manifest except `manifests/examples/ec2/s3-wheezy-amd64-pvm.yml` uses `cloud_init`, so the image-side failures hit custom manifests that rely on the documented default.
  - `install_init_scripts` is read in Python but missing from `manifest-schema.yml`. Add it while working on this.
  - The scripts run from native systemd units on stretch and newer, because systemd v260 removed SysV script support. `ec2-run-user-data.service` disables itself only when the script's final `insserv -r $0` succeeds, so on releases without insserv user-data still runs on every boot. The marker file above fixes that too.
  - If the build runs inside a container on the EC2 host, IMDSv2 needs `HttpPutResponseHopLimit` >= 2.
  - AWS defaults (unverified): AL2023 AMIs are registered with `ImdsSupport=v2.0`, accounts can default to IMDSv2-only, and instance types released since mid-2024 default to IMDSv2-only.
  - Workaround until this is fixed: set `HttpTokens=optional` on the build instance.

- **Effort:** medium; **Severity:** high

### Find the attached EBS volume by its volume ID so EBS builds work on Nitro hosts

- **Where:** `bootstrapvz/providers/ec2/ebsvolume.py:39-59`, `bootstrapvz/providers/ec2/tasks/ebs.py:27-34`, `bootstrapvz/base/fs/partitionmaps/abstract.py:67-73`, `bootstrapvz/base/fs/volume.py:88-89`, `bootstrapvz/providers/ec2/README.rst:6-8`

- **Problem:** `_before_attach` picks the first `/dev/xvd[f-z]` that does not exist locally and attaches the volume as the matching `/dev/sd<letter>`. It then sets `device_path` to the guessed `/dev/xvd<letter>`. The `volume_in_use` waiter only checks the API attachment state, not that a device node appeared. On Nitro instances, which include every current-generation type (t3, m5+, c5+, r5+), EBS volumes appear as `/dev/nvmeXn1` and `/dev/xvdf` never exists. kpartx/parted, mkfs and grub-install then run against a missing node, and the build fails after the volume was created and attached. In practice, EBS builds only work on previous-generation Xen types such as t2, m4 and c4.

- **Fix:**
  - After `attach_volume`, poll with a timeout for `/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_<volume id without the dash>` (Nitro) or the requested `/dev/xvd<letter>` (Xen).
  - Set `device_path` to `os.path.realpath()` of whichever appears first. It must be the real kernel name, not the symlink, because `link_dm_node` looks up `os.path.basename(device_path)` in `/proc/partitions` and the kpartx regex embeds `device_path` literally.
  - Choose the free API device name from the instance's `BlockDeviceMappings` (`describe_instances`) instead of checking for local `/dev/xvd*` nodes. On Nitro the local check always picks `sdf`, even if `sdf` is already attached.
  - kpartx names NVMe partitions `nvme1n1p1`, which the existing `.+[^\d](\d+)` regex already handles.
  - Document the supported build instance types in the README.

- **Notes:**
  - The by-id link comes from systemd's `60-persistent-storage.rules` (`nvme-$ID_MODEL_$ID_SERIAL`). That EBS reports the model "Amazon Elastic Block Store" and the serial `vol<id without dash>` is unverified.
  - An alternative is the `amazon-ec2-utils` udev rules on the host, which create `/dev/sdf`-style links, but that adds a host dependency.
  - A reviewer said the generated images hard-code `/dev/xvda`. That is true only for the PV/S3 paths (`S3FStab` in `tasks/filesystem.py:17`, pvgrub `40_custom` in `tasks/boot.py:53-60`). HVM images use `UUID=` in fstab and grub, so they boot from NVMe.
  - `prebootstrapped` attaches through the same `ebs.Attach` and benefits too.
  - The resulting AMI still needs ENA to launch on Nitro (see the next entry).

- **Effort:** medium; **Severity:** high

### Register HVM AMIs with ENA support and use the in-tree drivers

- **Where:** `bootstrapvz/providers/ec2/tasks/ami.py:126-128`, `bootstrapvz/providers/ec2/tasks/network.py:84-178`, `bootstrapvz/providers/ec2/__init__.py:49-50`, `bootstrapvz/providers/ec2/__init__.py:138-142`, `bootstrapvz/common/tasks/kernel.py:7-15`, `bootstrapvz/providers/ec2/manifest-schema.yml:23-29`, `bootstrapvz/providers/ec2/README.rst:108-147`

- **Problem:** `EnaSupport` and `SriovNetSupport` are set only when `enhanced_networking: simple`. Without that setting, HVM AMIs lack the ENA attribute and cannot launch on Nitro types ("Enhanced networking with the Elastic Network Adapter (ENA) is required"), which covers all current-generation instances. With `simple`, `resolve_tasks` also adds `AddDKMSPackages`, `InstallEnhancedNetworking` and `InstallENANetworking`, which download and compile out-of-tree drivers with DKMS:
  - ixgbevf 4.3.4 from 2018, from Intel's download mirror, hard-coded for every release >= stretch.
  - The ENA driver from the `master` tarball of amzn-drivers by default, which moves over time.

  Neither download has a checksum or a timeout, and any download or compile failure aborts the build in system_modification. A bookworm or trixie build must therefore compile a 2018 Intel driver against kernel 6.x. The image content also depends on whatever `master` was at build time, which breaks the rule that the manifest fully describes the image.

- **Fix:**
  - For HVM and release >= stretch, always register with `EnaSupport=True` and `SriovNetSupport='simple'`, and rely on the in-tree `ena` and `ixgbevf` modules.
  - In `resolve_tasks()`, add the DKMS tasks only for releases < stretch, so wheezy and jessie keep working.
  - Keep `enhanced_networking: none` as an explicit opt-out, or make `simple` the HVM default.
  - For the remaining DKMS path, pin the ENA default to a released tag instead of `master`, verify both downloads against a sha256, and pass a timeout.
  - Update the README (stretch+ uses the Debian kernel's drivers; `amzn-driver-version` only applies to old releases) and add a CHANGELOG entry.

- **Notes:**
  - ENA is in mainline since Linux 4.9 (`drivers/net/ethernet/amazon/Kconfig` exists in v4.9). Unverified: that Debian amd64 kernels build it since stretch, that ixgbevf 4.3.4 fails to compile against 6.x, and that the Intel URL still resolves.
  - This changes the output of `manifests/official/ec2/ebs-stretch-amd64-hvm.yml`, which uses `simple`.
  - The README lists `master` as a valid `amzn-driver-version`, but the schema pattern `^([0-9]+\.?){3}$` rejects it.
  - Related Nitro gaps to check with a test boot:
    - `AddXenGrubConsoleOutputDevice` (`tasks/boot.py:16`) adds `console=hvc0`, which does not exist on Nitro, where the serial console is `ttyS0`.
    - `common/tasks/network-configuration.yml` brings up only `eth0`, while ENA NICs may get predictable names such as `ens5` (unverified; cloud-init writes its own config).
  - Builds on Nitro hosts also need the EBS device fix (previous entry).

- **Effort:** medium; **Severity:** high

### Make the EC2 expand-root script grow the root filesystem

- **Where:** `bootstrapvz/common/tasks/initd.py:57-84`, `bootstrapvz/common/assets/init.d/expand-root:20-44`, `bootstrapvz/providers/ec2/__init__.py:72`, `bootstrapvz/providers/ec2/__init__.py:86-88`, `bootstrapvz/providers/ec2/__init__.py:97-98`, `bootstrapvz/providers/ec2/tasks/packages.py:22-32`, `bootstrapvz/plugins/cloud_init/__init__.py:39-42`, `bootstrapvz/plugins/expand_root/__init__.py:9-11`

- **Problem:** Three independent bugs stop the script from growing the root filesystem:
  1. `AdjustGrowpartWorkaround` (initd.py:84) replaces the line `growpart="growpart"` with the bare command `growpart-workaround` instead of `growpart="growpart-workaround"`. `$growpart` stays empty, `hash $growpart` with no argument succeeds, and line 31 runs `/dev/xvdf 1` as a command, which fails. This task is added for every release >= jessie.
  2. `AdjustExpandRootScript` (initd.py:71-72) writes `info.volume.device_path` into the script. That is the build host's attach path (`/dev/xvdf` or later), not the boot device: `/dev/xvda`, or `/dev/nvme0n1` on Nitro, where `${root_device_path}${root_index}` cannot express the `p1` suffix.
  3. With `partitions: type: none`, the adjust task is not added. The script keeps `root_index="0"`, probes `/dev/xvda0`, and never runs resize2fs.

  As a result, any EC2 image built without `cloud_init` never grows its filesystem when launched with a larger root volume. The failure only appears in syslog.

- **Fix:**
  - Find the root device at boot instead of baking it in: run `findmnt --noheadings --output SOURCE /`, then read `/sys/class/block/<name>/partition` for the index and the parent sysfs directory for the disk. Unlike `lsblk -o PKNAME`, this also works with old util-linux.
  - If root is a whole disk, skip growpart and only run resize2fs or xfs_growfs. `AdjustExpandRootScript` can then be removed.
  - Fix the substitution to `growpart="growpart-workaround"`. Better: for releases >= stretch, install `cloud-guest-utils`, which ships Debian's growpart, and keep the 2013 copy plus `AdjustGrowpartWorkaround` only for jessie, decided in `resolve_tasks()`.
  - Add a unit test that applies the sed tasks to the asset and checks the result.

- **Notes:**
  - This entry merges two overlapping audit findings.
  - Only the `cloud_init` plugin removes these tasks. It does not remove `AddWorkaroundGrowpart`, so `growpart-workaround` is still copied into the image.
  - The `expand_root` plugin is the maintained alternative (cloud-guest-utils plus a systemd unit) and discards these EC2 tasks when it is used. Letting EC2 reuse it would remove the duplicate script.
  - `AddWorkaroundGrowpart` is described as "for jessie" but runs for every release >= jessie.
  - expand-root runs from a native systemd unit (`bootstrapvz/common/assets/init.d/expand-root.service`) on stretch and newer.

- **Effort:** medium; **Severity:** medium

### Repair the PV (pvgrub) and S3 instance-store AMI path

- **Where:** `bootstrapvz/providers/ec2/tasks/ami.py:33-74`, `bootstrapvz/providers/ec2/tasks/ami.py:116-124`, `bootstrapvz/providers/ec2/tasks/ami-akis.yml:1-38`, `bootstrapvz/common/tools.py:108-112`, `bootstrapvz/providers/ec2/__init__.py:149-153`, `bootstrapvz/providers/ec2/manifest-schema-s3.yml:22-37`, `bootstrapvz/providers/ec2/tasks/host.py:6-15`, `bootstrapvz/providers/ec2/assets/certs/cert-ec2.pem`, `bootstrapvz/providers/ec2/README.rst:4-8`, `bootstrapvz/providers/ec2/README.rst:221-228`

- **Problem:**
  - **PV kernel lookup fails late.** For PV images (EBS or S3), `RegisterAMI` looks up the PV-GRUB kernel (AKI) in `ami-akis.yml`, but only in the image_registration phase, after the volume has been built and snapshotted. The table covers 12 regions. For any other region (for example us-east-2 or ap-northeast-2), `config_get` raises `AttributeError: 'NoneType' object has no attribute 'get'`. For ca-central-1/i386 it returns `None`, which boto3 rejects.
  - **The failure leaks a snapshot.** No rollback task removes the snapshot, so a billable snapshot is left behind.
  - **S3 builds need euca2ools.** The S3 path bundles and uploads with `euca-bundle-image` and `euca-upload-bundle` from euca2ools, which is Python 2 only: its `setup.py` lists Python 2.6/2.7 and imports `distutils`. It is also probably gone from Debian since bullseye (unverified). As a result, S3 builds stop at `CheckExternalCommands` on any current host.
  - **Frozen region data.** The S3 region enum is frozen at 13 regions, and `UploadImage` builds legacy `https://s3-{region}.amazonaws.com/` endpoints.
  - **Wrong certificate for gov/China regions.** `BundleImage` always passes the global `cert-ec2.pem` via `--ec2cert`, although euca2ools ships separate certificates for us-gov-west-1 and cn-north-1. Bundles for those regions, including `manifests/official/ec2/s3-wheezy-amd64-pvm-cn-north-1.yml`, are therefore probably encrypted for the wrong key (unverified).

- **Fix:** The maintainer decided to repair this path, not remove it.
  1. **Fail early.** Move the AKI lookup into a preparation-phase task that runs after `GetInstanceMetadata`/`SetRegion` and stores the result on `info._ec2`. Raise a `TaskError` naming the region and architecture when no AKI exists. For S3, also check in `validate_manifest`, where the region is known. Refresh `ami-akis.yml` from the AWS "user provided kernels" page.
  2. **Clean up on failure.** Add a rollback counterpart for `ebs.Snapshot` that deletes the snapshot.
  3. **Replace euca2ools.** There are two real options:
     - (a) Switch to AWS's own ec2-ami-tools (`ec2-bundle-image`, `ec2-upload-bundle`; Ruby, distributed by AWS as a zip and not packaged in Debian, unverified). Their long options largely match the ones euca2ools copied (unverified).
     - (b) Implement bundling in Python: tar and gzip, AES-128-CBC with a random key and IV, 10 MB parts, and a manifest XML with the key and IV encrypted to the user and EC2 certificates and signed with the user key; upload the parts with boto3's S3 client. This removes the host dependency but adds a crypto library (managed with uv) and more code.
  4. **Select the EC2 certificate per partition** (global, gov, cn) and ship the gov and cn certificates.
  5. **Replace the frozen region data.** Replace the S3 region enum with a pattern, and build endpoints as `https://s3.{region}.amazonaws.com/` (`.amazonaws.com.cn` for China).
  6. **Check that current releases boot under pv-grub:** Xen PV domU support in the Debian kernel, and whether pv-grub, which is based on GRUB legacy, can read ext4 created with current mke2fs defaults (`64bit`, `metadata_csum`) (unverified).
  7. **Update docs and verify.** Update the README (drop "a fix is in the works", document the new tool) and CHANGELOG, then verify with `tests/system/ec2_s3_pvm_tests.py` and `ec2_ebs_pvm_tests.py` once those are repaired (see the system tests section).

- **Notes:**
  - Keep the existing pvm examples, per the decision to keep old releases, and add hvm examples for current releases next to them.
  - The bundled `cert-ec2.pem` (CN "AES Test", expired Aug 2006) is byte-identical to the `cert-ec2.pem` that euca2ools itself ships, so it is the real EC2 bundling certificate. Bundling only uses its public key, so its expiry is most likely not what breaks this path.
  - When euca2ools is missing, S3 builds fail early in the validation phase. Only the AKI lookup fails late.
  - The README sentence "a fix is in the works" refers to S3 builds run outside EC2.
  - PV and instance-store root volumes only run on previous-generation Xen types such as m1, m3, c3 and t1, which newer regions do not offer (unverified). Weigh the effort against actual users.

- **Effort:** large; **Severity:** medium

### Make the secondary-ENI network scripts work on Debian

- **Where:** `bootstrapvz/providers/ec2/tasks/network.py:40-81`, `bootstrapvz/providers/ec2/__init__.py:78-79`, `bootstrapvz/providers/ec2/assets/ec2/ec2dhcp.sh:22-34`, `bootstrapvz/providers/ec2/assets/ec2/ec2net-functions:40-69`, `bootstrapvz/providers/ec2/assets/ec2/ec2net-functions:100-136`, `bootstrapvz/providers/ec2/assets/ec2/ec2net.hotplug:24`, `bootstrapvz/providers/ec2/assets/ec2/53-ec2-network-interfaces.rules:20-21`

- **Problem:** For every release > wheezy, with or without `cloud_init`, the provider installs Amazon Linux/RHEL scripts that never configure secondary ENIs on Debian:
  - **The DHCP hook never runs.** It is copied to `dhclient-exit-hooks.d/ec2dhcp.sh`, and Debian's dhclient-script finds hooks with `run-parts --list`, which skips names that contain a dot (checked locally). The file also only defines the RHEL-style `ec2dhcp_config`/`ec2dhcp_restore`, which nothing calls. So `rewrite_rules`/`rewrite_aliases` (per-ENI source policy routing and secondary IPs) never run.
  - **The hotplug path writes files ifupdown ignores.** It writes `/etc/sysconfig/network-scripts/ifcfg-ethN`, `route-ethN` and `/etc/dhcp/dhclient-ethN.conf`, so even if the rules ran they would point at an empty routing table.
  - **What does work:** udev runs `ec2net.hotplug`, which calls `ifup ethN` against the `iface ethN inet dhcp` stanzas.
  - **Effect:** with a second ENI in the same subnet, replies to traffic for that ENI's address leave through eth0 (asymmetric routing).

- **Fix:** Pick one of two options, and decide in `resolve_tasks()` whether to skip this when `cloud_init` handles multiple NICs.
  - (a) Rewrite the scripts for Debian:
    - Install the hook without a dot (`ec2dhcp`) and dispatch on `$reason`: BOUND, RENEW, REBIND and REBOOT apply the config; EXPIRE, FAIL, RELEASE and STOP remove it.
    - Run the logic in a bash subprocess. dhclient-script sources hooks under `/bin/sh`, while `ec2net-functions` uses bash arrays, `declare -A` and `let`, and calls `exit`, which would end dhclient-script itself.
    - Add the per-table default route with `ip route add ... table $RTABLE` instead of writing `route-ethN`.
    - Use IMDSv2 tokens, add `curl`, move the files out of `/etc/sysconfig`, and make the stanzas `allow-hotplug`.
  - (b) Drop the hook and the sysconfig files, keep `allow-hotplug ethN` stanzas, and point multi-ENI users to cloud-init or Debian's `amazon-ec2-net-utils`, if Debian packages it (unverified).

- **Notes:**
  - More breakage in the same scripts:
    - `get_meta` uses `curl`, which the provider never installs, and sends IMDSv1 requests without a token.
    - `ec2net.hotplug:24` exits unless `runlevel` reports 5.
    - The `RTABLE=${INTERFACE#eth}` arithmetic and the eth1-eth7 stanzas assume `ethN` names, while ENA NICs on Nitro may get predictable names (unverified).
  - The copy into `/etc/dhcp/dhclient-exit-hooks.d` raises `FileNotFoundError` if isc-dhcp-client is not installed. trixie installs dhcpcd-base instead (Debian dhcpcd changelog 1:10.0.10-2), so this task aborts every EC2 trixie build, including cloud_init builds, unless the manifest installs isc-dhcp-client. `manifests/examples/ec2/ebs-trixie-amd64-hvm.yml` does that as a workaround. Running the task against an image root without the directory confirms the crash. Check this first.
  - The audit's statement that "only the iface stanzas have an effect" overlooks the udev-triggered `ifup`.

- **Effort:** medium; **Severity:** low

### Pass only the set parameters in ec2_launch and drop EC2-Classic from its README

- **Where:** `bootstrapvz/plugins/ec2_launch/tasks.py:26-32`, `bootstrapvz/plugins/ec2_launch/manifest-schema.yml:9-21`, `bootstrapvz/plugins/ec2_launch/README.rst:3-22`, `manifests/official/ec2/ebs-stretch-amd64-hvm.yml:56-66`

- **Problem:**
  - **Omitted settings crash the launch late.** `LaunchEC2Instance` passes `SecurityGroupIds=...get('security_group_ids')` and `KeyName=...get('ssh_key')`. The README marks both as required, but the schema does not. If either is omitted, `run_instances` receives `None`, and botocore raises `ParamValidationError` before sending the request (checked locally with botocore 1.43.110). That happens in image_registration, after the full build and AMI registration, so the run is reported as failed even though the AMI exists.
  - **The README describes EC2-Classic.** It still describes launching an "AWS classic instance" with "security groups (not VPC)" and "AWS Classic capable" instance types, with `m3.medium` as the default. The code actually passes VPC security group IDs and launches into the default VPC.

- **Fix:**
  - Build the `run_instances` kwargs only from keys that are present. The API accepts a launch without `KeyName` (no key pair) and without `SecurityGroupIds` (the default security group), so the README can mark both as optional. If they should stay mandatory, add `required: [security_group_ids, ssh_key]` to the schema instead, so validation catches the omission.
  - Rewrite the README: VPC security group IDs, no EC2-Classic, a current default instance type, and fix "sinning up".
  - Choose the default instance type with the ENA entry in mind. A Nitro default such as `t3.micro` only works once AMIs carry `EnaSupport`; until then use a Xen type such as `t2.micro`, or make `instance_type` required.
  - Update the commented example in `ebs-stretch-amd64-hvm.yml` ("this have to be a NONE VPC SG", `m3.medium`).

- **Notes:**
  - `DeregisterAMI` was already ported to boto3 (`instance_running` waiter, `deregister_image`, `delete_snapshot`). With S3 backing nothing sets `info._ec2['snapshot']`, so `deregister_ami: true` raises `KeyError` after deregistering the AMI, and no order is defined against ec2_publish's `CopyAmiToRegions`/`PublishAmi`.
  - Accounts without a default VPC need a subnet; an optional `subnet_id` setting would cover them but does not exist today.
  - EC2-Classic was retired in 2023, and `m3` is a previous-generation type that newer regions do not offer (both unverified).
  - No active manifest uses this plugin.

- **Effort:** small; **Severity:** low

## Azure

### Install waagent from Debian and limit PatchUdev to old releases

- **Where:** `bootstrapvz/providers/azure/tasks/packages.py:31-60`, `bootstrapvz/providers/azure/tasks/packages.py:13-20`, `bootstrapvz/providers/azure/tasks/boot.py:8-20`, `bootstrapvz/providers/azure/__init__.py:27-29`, `bootstrapvz/providers/azure/assets/udev.diff`, `bootstrapvz/providers/azure/manifest-schema.yml:9-15`, `bootstrapvz/providers/azure/README.rst:14-33`, `manifests/examples/azure/wheezy.yml`, `manifests/examples/azure/jessie.yml`

- **Problem:** The `Waagent` task downloads `https://github.com/Azure/WALinuxAgent/archive/WALinuxAgent-<version>.tar.gz`, copies the single-file `waagent` script out of it to `/usr/sbin` and runs `chroot ... waagent -install`. The upstream repository no longer has any `WALinuxAgent-*` tag or branch. Releases are now tagged `v2.2.x` to `v2.16.x`, and the 2.0/2.1 code only survives on the branches `archive/2.0` and `archive/2.1`. So the download fails for every version, including the `2.0.14` in both example manifests and the `2.0.4` in the README. Even if the download worked, the 2.0 script starts with `#!/usr/bin/env python`, and bullseye+ images only get `python3-openssl`/`python3-pyasn1` and no `python` binary, so `waagent -install` would still fail there. On top of that, `PatchUdev` is added for every release and runs `patch` to delete a `sleep $ROOTDELAY` block from `scripts/init-top/udev`, a block Debian removed itself (the code comment cites Debian systemd commit 61e055638cea). Where the block is gone, `patch` exits 1 and the build aborts. Net effect: the provider cannot build an Azure image for any release, and CI cannot notice because the only Azure manifests are wheezy and jessie examples whose tasks the dry-run never executes.

- **Fix:**
  1. Install the agent as a Debian package with `info.packages.add('waagent')` in `DefaultPackages`. Microsoft's current "Prepare a Debian VHD for Azure" guide installs it with `apt install waagent`. Drop the wget/tar/cp/`waagent -install` steps. Keep the `conf` override and the `/etc/default/useradd` `SHELL` change in a task that runs after `InstallPackages`. When the `conf` path does not exist, reject it in `validate_manifest` instead of silently skipping it (`packages.py:51-52`).
  2. Make `waagent.version` optional in the schema and README, so existing manifests stay valid. Some releases may lack a Debian `waagent` package (check wheezy and jessie). For those, keep a download path, but fetch a ref that still exists. The single-file 2.0 agent now only lives on branch `archive/2.0` (head `bc2005a5273a`, `GuestAgentVersion = "WALinuxAgent-2.0.18"`). Pin a commit, verify a sha256, and declare the download tool in `info.host_dependencies` (or use `urllib` like the salt plugin). Update the examples' `version: 2.0.14`, which can no longer be downloaded.
  3. Add `boot.PatchUdev` in `resolve_tasks()` only for releases whose `init-top/udev` still has the ROOTDELAY sleep. Use the release objects in `releases.py` for this: wheezy certainly, jessie needs checking.
  4. Add a bookworm or trixie Azure example manifest, so the integration dry-run covers a current release.

- **Notes:** The audit's claim that `patch` can hang on an "Assume -R?" prompt is wrong. `log_check_call` pipes stdout, and GNU patch does not open `/dev/tty` when stdout is not a terminal, so it takes the default answer and exits 1. `wget` is not declared as a host dependency. Two things are unverified because debian.org was not reachable: which release first lacks the ROOTDELAY block, and which old releases ship `waagent`. Microsoft's Debian guide also recommends `hyperv-daemons` and `cloud-init`. Related but not covered here: the provider only installs `grub-pc`, so images are BIOS-only (Hyper-V Generation 1), and Microsoft's docs say remote NVMe needs a Generation 2 image. The README says the provider "generates raw images", but it produces VHDs.

- **Effort:** medium; **Severity:** high

### Create Azure VHDs at the exact requested size and attach them as vpc

- **Where:** `bootstrapvz/common/fs/virtualharddisk.py:11-17`, `bootstrapvz/common/fs/qemuvolume.py:46-51`, `bootstrapvz/base/fs/partitionmaps/gpt.py:97-104`, `bootstrapvz/providers/azure/manifest-schema.yml:24-38`

- **Problem:** `VirtualHardDisk._before_create` runs `qemu-img create -o subformat=fixed -f vpc <path> <N>M` without `force_size`. Without that option, qemu's vpc driver rounds the size up to the next CHS geometry (`calculate_rounded_image_size` in `block/vpc.c`), so the virtual size is almost never a whole MiB. For example, 10240M becomes 10,737,893,376 bytes (10240.45 MiB), and 20480M becomes 21,475,270,656 bytes, which is exactly the size in Microsoft's documented error "unsupported virtual size ... The size must be a whole number (in MBs)". Azure rejects such a VHD when an image or managed disk is created from it, even though the comment on line 11 states the 1 MiB requirement. Separately, `QEMUVolume._before_attach` runs `qemu-nbd --connect <dev> <image>` without `--format`, and qemu's `vpc_probe` only looks for `conectix` at offset 0. A fixed VHD has its only footer at the end of the file, so it is probed as raw and the NBD device exposes the footer as its last sector. With `partitions: type: gpt`, which the Azure schema allows, `parted mklabel gpt` writes the backup GPT header onto that sector, and the result is an invalid VHD with no error reported.

- **Fix:** Create the image with `-o subformat=fixed,force_size=on` (Microsoft documents `-o subformat=fixed,force_size` for qemu >= 2.6). Attach it with `['qemu-nbd', '--format=' + self.qemu_format, '--connect', self.loop_device_path, self.image_path]`. Every `QEMUVolume` subclass already defines `qemu_format` (qcow2, vdi, vmdk, vpc), and `_before_create` already uses it.

- **Notes:** Azure is the only user of `backing: vhd`. Contrary to the audit, VirtualBox does not use this class, because its schema only allows `raw`, `vdi` and `vmdk`. Nothing later resizes or converts the image (`minimize_size` refuses vhd, see `plugins/minimize_size/__init__.py:29-30`), so the create call sets the final size. The CHS rounding and the offset-0 probe were checked against qemu master. The rounded size lands on a whole MiB only by coincidence (cylinder counts that are multiples of 128). The shipped examples use `msdos`, which never writes the last sector, so footer corruption needs `gpt`. `none` is most likely unaffected, because filesystems are sized in whole 4 KiB blocks. The explicit format also removes qemu-nbd's "probing guessed raw" warning for all QEMU backings. An end-to-end Azure test also needs the waagent entry above.

- **Effort:** small; **Severity:** medium

## GCE

### Register GCE images with guest OS features and document the BIOS-only limit

- **Where:** `bootstrapvz/providers/gce/tasks/image.py:54-57`, `bootstrapvz/providers/gce/manifest-schema.yml:6-19`, `bootstrapvz/providers/gce/tasks/boot.py:15-18`, `bootstrapvz/common/tasks/grub.py:11-17`, `bootstrapvz/providers/gce/tasks/packages-kernels.yml`, `bootstrapvz/providers/gce/README.rst`

- **Problem:** `RegisterImage` runs `gcloud compute images create <name> --source-uri=... --description=...` and never passes `--guest-os-features`, and the schema has no setting for it. Guest OS features can only be set when an image is created, so a registered image cannot use gVNIC networking. Third-generation and newer series (C3, C3D, C4, N4 and others) only offer gVNIC, so the images cannot run there (unverified, the current GCE docs could not be fetched). The image is not tagged `VIRTIO_SCSI_MULTIQUEUE` either, although `boot.py` enables `scsi_mod.use_blk_mq=Y` for stretch+. The only boot paths are `grub-pc` and extlinux (both BIOS-only), and the kernel table is amd64 only, so Shielded VM and Confidential VM (which need `UEFI_COMPATIBLE`) and the Arm series (T2A, C4A) are out of reach. Older series (E2, N1, N2, N2D) still boot the images with virtio-net.

- **Fix:** Add an optional `guest_os_features` array of strings to the GCE schema. When it is set, pass it as `--guest-os-features=<comma separated list>` in `RegisterImage`. Document it in the README with release-appropriate values: `VIRTIO_SCSI_MULTIQUEUE` for stretch+, plus `GVNIC` for bullseye+. Alternatively, derive that default from the release with the `releases.py` objects. Either way, never add `GVNIC` unconditionally. Also state in the README that images are BIOS-only and amd64-only. A UEFI path (`grub-efi-amd64`, GPT with an EFI system partition, then `UEFI_COMPATIBLE`) and arm64 support are separate feature work.

- **Notes:** The Terraform Google provider docs for `google_compute_image` list these feature names: `MULTI_IP_SUBNET`, `SECURE_BOOT`, `SEV_CAPABLE`, `UEFI_COMPATIBLE`, `VIRTIO_SCSI_MULTIQUEUE`, `WINDOWS`, `GVNIC`, `IDPF`, `SEV_LIVE_MIGRATABLE`, `SEV_SNP_CAPABLE`, `SUSPEND_RESUME_COMPATIBLE`, `TDX_CAPABLE`, `SEV_LIVE_MIGRATABLE_V2` and `SNP_SVSM_CAPABLE`. The list keeps growing, so a pattern such as `^[A-Z0-9_]+$` is easier to maintain than an `enum`. A reviewer pointed out that unconditional `GVNIC` is wrong for buster and older: the `gve` driver entered Linux in 5.3, and buster ships 4.19 (unverified). None of the official GCE manifests sets `gce_project`, so CI never runs this code. `gcloud` and `gsutil` are not declared as host dependencies. One reviewer rated this low because older series still work.

- **Effort:** medium; **Severity:** medium

### Fix the gcs_destination example and reject a bare bucket

- **Where:** `bootstrapvz/providers/gce/tasks/image.py:39-40`, `bootstrapvz/providers/gce/tasks/image.py:56`, `bootstrapvz/providers/gce/README.rst:22`, `bootstrapvz/providers/gce/README.rst:34`, `bootstrapvz/providers/gce/manifest-schema.yml:11`

- **Problem:** `UploadImage` and `RegisterImage` build the object URI as `gcs_destination + tarball_name` with no separator. `tarball_name` is `<lowercased image name>.tar.gz`, which is `disk.tar.gz` for the official GCE manifests. The README example is `gcs_destination: gs://my-bucket` and never mentions a trailing slash, so copying it produces `gs://my-bucketdisk.tar.gz`. That names a bucket that does not exist, so `gsutil cp` fails in the image_registration phase, after the whole image has been built. The schema accepts any string.

- **Fix:** Keep the plain concatenation, so prefixes such as `gs://bucket/images/debian-` keep working. Change the README example to `gs://my-bucket/` and document that the value is a prefix to which `<image name>.tar.gz` is appended. Add a schema pattern that requires a `/` after the bucket name, for example `pattern: ^gs://[^/]+/`. Moving from `gsutil cp` to `gcloud storage cp` is optional and separate.

- **Notes:** Two of the audit's suggestions do not work. The `rstrip('/') + '/'` join would break prefix use, and the pattern `^gs://[^/]+(/.*)?$` still accepts the bare `gs://my-bucket`. No official manifest sets `gcs_destination`, so CI does not cover this. Workaround until fixed: write `gs://my-bucket/`. Both reviewers rated this low, not medium.

- **Effort:** small; **Severity:** low

## Oracle

### Port the Oracle provider to the current OCI API and fail on verify mismatches

- **Where:** `bootstrapvz/providers/oracle/apiclient.py:18`, `bootstrapvz/providers/oracle/apiclient.py:28-41`, `bootstrapvz/providers/oracle/apiclient.py:52-67`, `bootstrapvz/providers/oracle/apiclient.py:101-136`, `bootstrapvz/providers/oracle/tasks/api.py:6-19`, `bootstrapvz/providers/oracle/tasks/image.py:8-59`, `bootstrapvz/providers/oracle/__init__.py:7-40`, `bootstrapvz/providers/oracle/manifest-schema.yml:9-28`, `bootstrapvz/providers/oracle/README.rst:4-61`, `manifests/official/oracle/jessie.yml`

- **Problem:** The upload client talks to `https://<identity-domain>.storage.oraclecloud.com`, logs in with Swift v1 (`/auth/v1.0`, `X-Storage-User: Storage-<identity-domain>:<user>`) and writes to `/v1/Storage-<identity-domain>/<container>/...`. That is the identity-domain based Oracle Storage Cloud Service (OCI Classic), which has been retired (unverified). If so, every manifest with `credentials` fails at `api.Connect` in the preparation phase, and the README still describes the "Oracle Compute Cloud Service dashboard". Separately, `compare_files` only logs `File hashes mismatch` and returns. `CompareImageTarballs` then deletes the downloaded copy and the run ends with "Successfully completed bootstrapping", so `verify: true` never stops a build with a corrupt upload. `auth_token` is a property that sends a new auth request on every access: once per uploaded 50/100 MiB chunk (line 123), plus once each in `create_manifest` and `download_file`. Building the tarball without `credentials` still works.

- **Fix:** Repair the provider, do not remove it.
  1. Do these small, independent fixes first. Make `compare_files` raise via `self._fail('File hashes mismatch')`. Fetch the token once: cache it on the client and re-authenticate on a 401.
  2. Port the upload to OCI Object Storage. New manifest keys (region, namespace, bucket, and the credentials the chosen API needs) replace `identity-domain`/`container`. `validate_manifest` should reject the old Classic keys with a message that names the new ones. There are three real options for the client:
     - The S3-compatible endpoint (`https://<namespace>.compat.objectstorage.<region>.oraclecloud.com`, with Customer Secret Keys) through boto3. boto3 is already a runtime dependency and handles multipart uploads.
     - The Swift-compatible endpoint (`https://swiftobjectstorage.<region>.oraclecloud.com`, user `<namespace>:<user>`, an auth token as password). This is closest to the current code, but it is unverified whether it supports large objects via `X-Object-Manifest`.
     - The `oci` Python SDK. It adds a dependency, but it is the only option that can also import the image (`ComputeClient.create_image`).
  3. OCI only imports custom images as `QCOW2` or `VMDK` (`ImageSourceDetails.source_image_type` in oci-python-sdk), not as the raw-in-`.tar.gz` format Classic accepted. Allow `backing: qcow2` (and possibly `vmdk`) in the schema next to `raw`, and for those backings upload the image file directly instead of the tarball.
  4. Rewrite the README for OCI. Add an example manifest for a current release (for example trixie with qcow2), so the dry-run covers the new schema.

- **Notes:** The endpoint formats and the Classic retirement come from memory and are unverified, because oraclecloud.com was not reachable. `manifests/official/oracle/jessie.yml` has no `credentials`, so it stays valid through the credentials schema change. That manifest uses `cloud_init` with `metadata_sources: Ec2`. cloud-init has a dedicated `Oracle` datasource (`cloudinit/sources/DataSourceOracle.py`), which an OCI example should use. Request timeouts are already in place (`REQUEST_TIMEOUT`).

- **Effort:** large; **Severity:** medium

## Plugins to repair

### Repair the chef plugin with Cinc Client on current releases

- **Where:** `bootstrapvz/plugins/chef/tasks.py:22-28`, `bootstrapvz/plugins/chef/tasks.py:31-38`, `bootstrapvz/plugins/chef/__init__.py:9-13`, `bootstrapvz/plugins/chef/manifest-schema.yml:9-14`, `docs/conf.py:278`

- **Problem:** `AddPackages` only calls `info.packages.add('chef')`, without an apt source or a key. Debian no longer ships a `chef` package: it was in stretch and probably buster, and it is gone from bullseye, bookworm and trixie (unverified, debian.org was not reachable). So on every current release, a manifest with the plugin passes validation and dry-run, then fails at `InstallPackages` with "Unable to locate package chef", after debootstrap and volume creation have already run. The plugin has no `README.rst`, so `docs/conf.py` generates no docs page for it, and no manifest or test uses it. The check `if 'assets' in manifest.plugins['chef']` in `__init__.py:11` is always true, because the schema requires `assets`.

- **Fix:** Keep the Debian `chef` package for the releases that ship it, and decide the boundary in `resolve_tasks()` with the `releases.py` objects. For newer releases, install Cinc Client, the Apache-2.0 rebuild of Chef Infra Client. Its distribution README lists builds for Debian 9+. Download a pinned omnibus `.deb` from `packages.cinc.sh` (path pattern `https://packages.cinc.sh/files/stable/cinc/<version>/debian/<major>/...`, exact file name unverified). Check it against a sha256 from the manifest and install it with `info.packages.add_local()`. Omnibus packages bundle their own Ruby, so plain `dpkg --install` without dependency resolution should be enough (unverified). Add `version` and `sha256` schema keys. Require them in `validate_manifest` when the release has no Debian package, the same way `openvox` checks `SUPPORTED_RELEASES`. Cinc reads `/etc/cinc`, not `/etc/chef`, so on that path `CopyChefAssets` must copy to `/etc/cinc`. Add a `README.rst` and an example manifest, so the docs and the integration dry-run cover the plugin.

- **Notes:** The Cinc config directory comes from `chef-utils/lib/chef-utils/dist.rb` on the `stable/cinc` branch of `gitlab.com/cinc-project/upstream/chef` (`DIR_SUFFIX = "cinc"`, client binary `cinc-client`). According to the `chef/mixlib-install` README, downloads of Progress' own Chef products now go through licensed (commercial or trial) APIs. That makes them unsuitable as the default for an unattended, shareable manifest. Cinc also publishes an install script at `omnitruck.cinc.sh`, but piping a moving script would make the image depend on the build date. The audit's "no supported release has it" goes too far: the repo still has wheezy, jessie and stretch manifests, and `chef` existed there (unverified).

- **Effort:** medium; **Severity:** medium

### Repair the opennebula plugin with the upstream one-context package

- **Where:** `bootstrapvz/plugins/opennebula/__init__.py:3-9`, `bootstrapvz/plugins/opennebula/tasks.py:6-17`, `bootstrapvz/plugins/opennebula/README.rst:4-5`

- **Problem:** `AddONEContextPackage` adds `opennebula-context`: from wheezy-backports on wheezy, and from the main archive on every other release. No Debian release after the jessie era ships that package (unverified). The plugin has neither a `manifest-schema.yml` nor a `validate_manifest`. So a bullseye, bookworm or trixie manifest with `opennebula: {}` passes validation and debootstrap, then fails at `InstallPackages` with "Unable to locate package opennebula-context". The README links to OpenNebula 4.2 documentation. No manifest uses the plugin, so CI cannot notice.

- **Fix:** Keep the existing wheezy path. For releases without a Debian package, install OpenNebula's own contextualization package `one-context`. Upstream now builds it in `github.com/OpenNebula/one-apps` under `context-linux/` (the old `addon-context-linux` repository is archived) and attaches it to the GitHub releases (latest tag `v7.4.0`). Add a schema with `version` and `sha256`, then download `one-context_<version>-<release>.deb`, verify it and install it. Its deb dependencies (from `context-linux/targets.sh`) are `util-linux bash curl bind9-host cloud-utils parted ruby ifupdown|ifupdown2 acpid|systemd sudo passwd dbus openssh-server gawk virt-what`. `info.packages.add_local()` runs plain `dpkg --install`, which does not resolve dependencies. So either add the dependencies with `info.packages.add()` first, or install with `apt-get install ./one-context_*.deb` in the chroot. `one-context` replaces and conflicts with `cloud-init` and `cloud-init-base`, so `validate_manifest` must reject combining this plugin with `cloud_init`. Update the README (current docs link, context variables) and add an example manifest.

- **Notes:** The deb file name follows `${NAME}_${VERSION}-${RELEASE}.deb` from `context-linux/generate.sh`. The exact asset URL, for example `https://github.com/OpenNebula/one-apps/releases/download/v7.4.0/one-context_7.4.0-1.deb`, is unverified. Which Debian release last shipped `opennebula-context` is also unverified, and should be checked before setting the release boundary in `resolve_tasks()`. Users of the `cloud_init` plugin can already pick cloud-init's `OpenNebula` datasource (`cloudinit/sources/DataSourceOpenNebula.py`) via `metadata_sources`. That is worth documenting, but it does not replace the plugin.

- **Effort:** medium; **Severity:** medium

### Repair the salt plugin for the current salt-bootstrap script

- **Where:** `bootstrapvz/plugins/salt/manifest-schema.yml:17-24`, `bootstrapvz/plugins/salt/tasks.py:10`, `bootstrapvz/plugins/salt/tasks.py:23-50`, `bootstrapvz/plugins/salt/__init__.py:4-6`, `bootstrapvz/plugins/salt/README.rst:11-19`

- **Problem:** The plugin downloads `releases/latest/download/bootstrap-salt.sh` at build time and appends `install_source` to the command unchanged. The current script (v2026.10.08) only accepts `latest|default|stable|testing|git|onedir|onedir_rc` and exits with `Installation type "daily" is not known...`. So `install_source: daily`, which the schema and README still offer, passes validation and then fails in package_installation. `version` is only passed for `git`, although the script supports `stable <version>` (for example `3006`, `3007.1` or `latest`), so `install_source: stable, version: '3006.9'` silently installs the latest Salt. The script also calls `__check_end_of_life_versions` unconditionally and exits with "End of life distributions are not supported." for Debian < 12 (in v2025.02.24 the limit was < 11). With the latest script, the plugin therefore cannot build bullseye or any older release. And because the script comes from `releases/latest`, the same manifest builds a different image, or fails, depending on the build date.

- **Fix:** Pin the script to a tag with a sha256 check, for example `https://raw.githubusercontent.com/saltstack/salt-bootstrap/v2026.10.08/bootstrap-salt.sh`. Let manifests override it (for example `bootstrap_version` and `bootstrap_sha256`), so manifests for older releases can pin an older tag. Remove `daily` from the enum and the README; such manifests already fail today. Pass `version` for `stable` as well as for `git`, and document the accepted formats. In `validate_manifest`, reject releases that the pinned script refuses (currently Debian < 12) unless the manifest pins an older script, so the failure happens at validation time. Add an example manifest.

- **Notes:** For `stable`, the script switches to onedir packages from `packages.broadcom.com`, and versions older than 3006 are not available there. Whether an older script tag still finds packages for bullseye, buster or older is unverified, and `git` may be the only route for old releases. The `sed_i` that renames `install_debian_check_services` still matches, because the function exists in v2026.10.08. The README documents `version` as git-only, so ignoring it for `stable` was documented behaviour, but the schema accepts the combination without a warning. Severity raised from the audit's low to medium, because the EOL check makes the plugin unusable on bullseye and older.

- **Effort:** small; **Severity:** medium

## System tests

### Use boto3's `console_output()` in the EC2 system tests

- **Where:** `tests/system/ec2_ebs_hvm_tests.py:23`, `tests/system/ec2_ebs_pvm_tests.py:22`, `tests/system/ec2_s3_pvm_tests.py:25`, `tests/system/providers/ec2/__init__.py:69-70`, `tests/system/providers/ec2/__init__.py:104`

- **Problem:** All 25 EC2 test bodies end with `print(instance.get_console_output().output)`, which is the boto 2 API: 13 in `ec2_ebs_hvm_tests.py` (lines 23-131), 9 in `ec2_ebs_pvm_tests.py` (lines 22-94) and 3 in `ec2_s3_pvm_tests.py` (lines 25, 34, 43). Since the boto3 port (48d3f30), `run_instance()` yields a boto3 `ec2.Instance`, which only has `console_output()`. The `get_console_output()` at line 69 is a local helper and is never attached to the instance (checked: `hasattr(ec2.Instance('i-1'), 'get_console_output')` is False with boto3 1.43). So every EC2 system test raises `AttributeError` only after the AMI has been built and registered, the VPC, subnet and instance have been created and the boot check has passed. The `finally` blocks still clean up, so nothing leaks, but every paid run is reported as a failure.

- **Fix:** Replace the 25 calls with `print(instance.console_output().get('Output'))`. botocore already base64-decodes `Output` (its `decode_console_output` handler), and the boot check in `run_instance` relies on that too. Another option is to have `run_instance` yield the console text, but the one-line replacement is simpler.

- **Notes:** The CI check proposed under "Fix the Docker system-test partial" below can catch this kind of API mistake if its stub mirrors the real instance type.

- **Effort:** small; **Severity:** medium

### Make the S3-backed EC2 system tests start

- **Where:** `tests/system/ec2_s3_pvm_tests.py:7-15`, `tests/system/providers/ec2/__init__.py:13-19`, `tests/system/tools/__init__.py:40-42`, `bootstrapvz/providers/ec2/manifest-schema-s3.yml:19-21`, `bootstrapvz/base/manifest.py:106-115`, `tests/system/README.rst:42`, `tests/system/README.rst:55`

- **Problem:** Each of three separate bugs is enough to stop the S3 tests:
  1. The `s3_pvm` partial sets `provider.bucket` but not `provider.region`, which `manifest-schema-s3.yml` requires. All three S3 tests therefore fail in `Manifest(data=...)` with `'region' is a required property` (reproduced offline).
  2. With a region added, the manifest validates, but `prepare_bootstrap` reads `manifest.image['region']` and `manifest.image['bucket']`. `Manifest.parse()` sets no `image` attribute (that section belongs to the pre-v1 manifest format), so this raises `AttributeError`.
  3. `apply_build_settings` writes the documented `s3-region` build setting to `manifest_data['image']['region']`. That raises `KeyError: 'image'` for every S3 test once `s3-region` is set in `system-tests.yml`, as the README example does.

  Only maintainers who run `tox -e system` are affected.

- **Fix:**
  - Add `region:` to the partial, for example `us-east-1`, or a value that `s3-region` overrides.
  - In `prepare_bootstrap`, read `manifest.provider['region']` and `manifest.provider['bucket']`.
  - In `apply_build_settings`, write `manifest_data['provider']['region'] = build_settings['s3-region']`.
  - State in `tests/system/README.rst:55` whether `s3-region` always overrides the region or only fills it in when unset.

- **Notes:** The S3/instance-store build path is itself broken (expired certificate, euca2ools). It is to be repaired, not removed; see the EC2 PV/S3 entry under Cloud providers. Fix these tests together with that repair. The S3 tests boot `m1.small` and the EBS PVM tests boot `t1.micro`. Whether AWS still offers these previous-generation PV types in the test region is unverified.

- **Effort:** small; **Severity:** low

### Fix the Docker system-test partial and validate system-test manifests in CI

- **Where:** `tests/system/docker_tests.py:8`, `bootstrapvz/providers/docker/manifest-schema.yml:22-27`, `tox.ini:2`, `tox.ini:34-36`, `.github/workflows/ci.yml:16`

- **Problem:** The Docker partial sets `dockerfile: CMD /bin/bash` as a string, but the schema declares `dockerfile` as an array of strings. `test_stable` therefore fails in `Manifest(data=...)` with `'CMD /bin/bash' is not of type 'array'` and never reaches the build. The provider README and every `manifests/examples/docker/*.yml` use the list form. This failure and the S3 validation failure need no root or cloud access to detect. But `tests/system` is only collected by `tox -e system`, which is neither in `envlist` nor in the CI matrix. With `boot_manifest` stubbed to validate and dry-run, all 13 VirtualBox, 13 EBS-HVM and 9 EBS-PVM combinations pass today. Only Docker (1 test) and S3 (3 tests) fail.

- **Fix:**
  - Change the partial to `dockerfile: [CMD /bin/bash]`.
  - Add a unit test that runs in `tox -e unit`. It should import each `tests/system/*_tests.py` module, replace that module's `boot_manifest` with a context manager that runs `Manifest(data=...)` and `run(manifest, dry_run=True)`, and call every `test_*` function.
  - To also catch mistakes like `get_console_output()`, the stub can yield an object with only the real instance's attributes. For EC2 that is a boto3 `ec2.Instance` with a botocore `Stubber` on its client.

- **Notes:** Importing the system-test modules needs neither `system-tests.yml` nor the `virtualbox` package (checked). The settings file is only loaded inside `boot_manifest`, and provider modules are imported there too.

- **Effort:** small; **Severity:** low

### Fix `apply_build_settings` dropping `apt_proxy` and mishandling `guest_additions`

- **Where:** `tests/system/tools/__init__.py:29-43`, `tests/system/README.rst:31-35`, `tests/system/README.rst:50-53`

- **Problem:** `apply_build_settings` applies the per-host `build_settings` from `system-tests.yml` to each test manifest. It was moved unchanged from the removed `bootstrapvz/remote/build_servers/build_server.py` in 53b7b06.
  - Line 33, `manifest_data.get('plugins', {})['apt_proxy'] = ...`, writes into a throwaway dict when the manifest has no `plugins` section, so the proxy is silently ignored. Today that hits `docker_tests.py`, the only test that does not use the `root_password` partial.
  - Lines 30-31 copy `build_settings['guest_additions']` only when the manifest already sets `provider.guest_additions`, and no VirtualBox partial does. So the documented setting never takes effect.
  - If a manifest did set `guest_additions` while `system-tests.yml` did not, lines 30-31 would raise `KeyError: 'guest_additions'` (reproduced). The condition is inverted.

- **Fix:**
  - Use `manifest_data.setdefault('plugins', {})['apt_proxy'] = ...`.
  - For VirtualBox manifests, check `'guest_additions' in build_settings` instead of checking the manifest.
  - Add unit tests for `apply_build_settings`. It is a pure dict transform and can run in `tox -e unit`.

- **Notes:** The `s3-region` `KeyError` in the same function is covered by the S3 entry above.

- **Effort:** small; **Severity:** low

### Repair or delete the `stable86` partial and the `x86` VirtualBox OS-type key

- **Where:** `tests/system/manifests/stable86.yml:3-4`, `tests/system/providers/virtualbox/instance.py:25-26`, `bootstrapvz/base/manifest-schema.yml:47-48`, `bootstrapvz/base/__init__.py:24-26`

- **Problem:** `stable86.yml` sets `architecture: x86`, which is not in the schema enum `[i386, amd64, arm64]`. Any manifest merged from it fails validation. It has been invalid since it was added upstream, and no test uses it. Changing it to `i386` would still fail, because `stable` is now trixie and i386 is rejected for trixie and later. `VirtualBoxInstance.create()` maps `{'x86': 'Debian', 'amd64': 'Debian_64'}` on `manifest.system['architecture']`, which can never be `x86`. An i386 or arm64 VirtualBox test would therefore pass `os_type_id=None` to `create_machine`. Nothing fails today because every VirtualBox test is amd64.

- **Fix:**
  - Key the OS-type map on `i386` instead of `x86`.
  - Replace `stable86.yml` with a valid 32-bit partial pinned to `release: bookworm`. Do not use `oldstable`, which becomes trixie once forky is released.
  - Add a VirtualBox i386 test that uses the new partial. Old releases stay supported, so extending coverage is preferred over deleting the partial.

- **Effort:** small; **Severity:** low

## Docs and infrastructure

### Add a Read the Docs config and publish the fork's docs

- **Where:** `docs/conf.py:106-110`, `docs/conf.py:298-301`, `docs/conf.py:307-308`, `pyproject.toml:46`, `AGENTS.md:37`, `README.rst:46`, `README.rst:67`, `README.rst:192-194`, `bootstrapvz/plugins/README.rst:7`

- **Problem:** AGENTS.md says the docs are published on Read the Docs. But the repository has no `.readthedocs.yaml` and never had one, so the fork's docs cannot be built there.
  - `conf.py` sets `html_theme = 'sphinx_rtd_theme'` only when not on RTD, so it relies on RTD to inject the theme. `if on_rtd: pass` is dead code.
  - The build imports every provider and plugin task module, through `taskoverview.generate_graph_data()` and autodoc. It therefore needs the package's runtime dependencies (for example `requests` from `bootstrapvz/providers/oracle/apiclient.py:4`), not just the `docs` dependency group. `tox -e docs` only works because tox installs the package.
  - README.rst and the plugins README link to `bootstrap-vz.readthedocs.org/en/master`, which is upstream's project. The fork's GitHub homepage field also still points to `http://bootstrap-vz.readthedocs.io/`.

- **Fix:**
  - Add a `.readthedocs.yaml` with:
    - `version: 2`
    - `build.os: ubuntu-24.04` and `build.tools.python: "3.13"`
    - `sphinx.configuration: docs/conf.py`
    - `sphinx.fail_on_warning: true`, which matches `-W` in tox
    - `python.install: [{method: uv, command: sync, groups: [docs]}]`, which installs the project, its runtime dependencies and the `docs` group from `uv.lock`
  - Set `html_theme = 'sphinx_rtd_theme'` unconditionally and remove `on_rtd` and both of its branches.
  - Decide where the docs live. Either claim or create an RTD project for the fork, then update the README links (https, `.io`) and the GitHub homepage field. Or, if the docs will not be published, remove the RTD claim from AGENTS.md and the links to upstream's docs.

- **Notes:**
  - Checked in the readthedocs.org repository docs on GitHub: `method: uv` with `command: sync` and `groups` is supported, and `build.os` and `sphinx.configuration` are required keys. `method: uv` allows only one `python.install` entry.
  - Unverified: that RTD stopped injecting `html_theme` (reportedly in October 2024).
  - Unverified: who owns the `bootstrap-vz` RTD project and what it serves today, presumably upstream's docs with remote bootstrapping and the puppet plugin. If upstream owns the slug, the fork needs a different one.

- **Effort:** small; **Severity:** low

### Make the documented non-root dry-run work and fix the logging docs

- **Where:** `AGENTS.md:52`, `bootstrapvz/base/main.py:13-19`, `bootstrapvz/base/main.py:41-42`, `bootstrapvz/base/main.py:66-72`, `bootstrapvz/base/log.py:39-42`, `docs/logging.rst:4`, `docs/developers/switches.rst`

- **Problem:** AGENTS.md documents `uv run bootstrap-vz --dry-run manifests/examples/kvm/buster-cloudimg.yml  # no root, no side effects`.
  - `main()` does skip the root check for `--dry-run`. But `setup_loggers()` always adds a file handler unless `--log -` is given, `--log` defaults to `/var/log/bootstrap-vz`, and `get_file_handler()` calls `os.makedirs()` and `logging.FileHandler()` on that path.
  - As a non-root user, the command fails before the manifest is loaded, with `PermissionError: [Errno 13] Permission denied: '/var/log/bootstrap-vz/<timestamp>_buster-cloudimg.log'` (reproduced as uid 65534).
  - As root, it writes a log file under `/var/log`, so "no side effects" is wrong as well.
  - CI cannot notice, because the integration tests call `run(manifest, dry_run=True)` directly.
  - `docs/logging.rst:4` says each run creates a logfile in "the `logs/` directory", which nothing uses, and `docs/developers/switches.rst` does not document `--log`.

- **Fix:**
  - Change the AGENTS.md command to `uv run bootstrap-vz --log - --dry-run manifests/examples/kvm/buster-cloudimg.yml`.
  - Rewrite `docs/logging.rst` to describe the `/var/log/bootstrap-vz` default and the `--log <path>|-` switch, and add `--log` to `switches.rst`.
  - Optionally, change the code to skip file logging with a console warning when a non-root dry-run cannot write the log directory. That only changes behaviour in a case that currently crashes.

- **Effort:** small; **Severity:** low

### Fix broken links in the built docs and rewrite the testing overview

- **Where:** `README.rst:15`, `CONTRIBUTING.rst:15`, `tests/README.rst:1-6`, `docs/testing/index.rst:4-12`, `docs/transform_github_links.py:9-21`, `docs/transform_github_links.py:25-37`, `docs/transform_github_links.py:87-90`, `CHANGELOG.rst:218`

- **Problem:** `transform_github_links` turns repo-relative links in included rst files into links to docs pages. Links it has no mapping for are left unchanged.
  - **No mapping:** `LICENSE <LICENSE>` (README.rst, built as `index.html`) and `.github/workflows/ci.yml` (CONTRIBUTING.rst) have no mapping. The built pages therefore link to targets that do not exist in the HTML output.
  - **Missing page:** `integration tests <integration>` in `tests/README.rst` and the old changelog link to `tests/integration` both map to `testing/integration_tests.html`. There is no `docs/testing/integration_tests.rst` and no `tests/integration/README.rst`.
  - **Wrong text:** `tests/README.rst` is the Testing landing page. It says the framework has two parts and that the integration tests bootstrap and boot images. In fact `tests/integration/dry_run_tests.py` only dry-runs every manifest. Building and booting is what the billable system tests do, and the intro never mentions them.
  - **Latent:** `includes_mapping` maps to `manifest/` and `manifest/official/` (singular). This has no effect today because every link in `manifests/README.rst` starts with `../`.

  `sphinx-build -W` does not check these link targets.

- **Fix:**
  - In `transform_github_links`, when there is no docs mapping but the target exists in the repository, rewrite the link to `https://github.com/kevin-olbrich/bootstrap-vz/blob/master/<path>`. Leave in-page anchors unchanged; they also fall through today.
  - Rewrite `tests/README.rst` to describe three suites:
    - unit tests
    - integration tests: a dry-run of every manifest in `manifests/`, needing no root or network
    - system tests: real builds and boots as root on a build host with `system-tests.yml`, which cost money
  - Either add `docs/testing/integration_tests.rst` with a `tests/integration/README.rst`, or drop the `<integration>` link.
  - Fix `manifest/` to `manifests/` in `includes_mapping`.

- **Notes:** The changelog link is in a 2015 entry and means what are now the system tests, so pointing it at the system tests page is enough. A small script that checks every relative `href` under `docs/_build/html` would keep these from coming back.

- **Effort:** small; **Severity:** low

### Regenerate the docs include files on every build

- **Where:** `docs/conf.py:269-293`, `docs/plugins/.gitignore`, `docs/providers/.gitignore`, `docs/testing/system_test_providers/.gitignore`

- **Problem:** `conf.py` writes `docs/providers/<name>.rst`, `docs/plugins/<name>.rst` and `docs/testing/system_test_providers/<name>.rst`. Each holds a single `.. include::` of that component's README. They are written only if the file does not exist yet, and never deleted. They are gitignored, so `git pull` neither removes them nor shows them, and the `:glob:` toctrees pick up every leftover file. After a component is removed or renamed, as `bootstrapvz/plugins/puppet` was in 00240da, a checkout that built the docs before keeps the stale include. `tox -e docs` then fails with `Problems with "include" directive path ... puppet/README.rst`. CI and RTD build from fresh checkouts, so only local builds are affected.

- **Fix:** Before generating, delete every `*.rst` except `index.rst` in the three directories. Alternatively, rewrite all of them on each build and delete those whose README is gone.

- **Effort:** small; **Severity:** low

### Update stale provider and plugin READMEs and dead links

- **Where:** `bootstrapvz/plugins/vagrant/README.rst:17-18`, `bootstrapvz/plugins/vagrant/__init__.py:9`, `bootstrapvz/plugins/vagrant/tasks.py:132`, `bootstrapvz/plugins/minimize_size/README.rst:14`, `bootstrapvz/plugins/minimize_size/README.rst:20-28`, `bootstrapvz/plugins/minimize_size/README.rst:41-50`, `bootstrapvz/plugins/minimize_size/manifest-schema.yml:11`, `bootstrapvz/plugins/minimize_size/tasks/shrink.py:28`, `bootstrapvz/providers/virtualbox/README.rst:12-15`, `bootstrapvz/providers/docker/README.rst:8-14`, `bootstrapvz/providers/docker/README.rst:36-43`, `bootstrapvz/plugins/unattended_upgrades/README.rst:5`, `bootstrapvz/plugins/cloud_init/README.rst:5`, `bootstrapvz/plugins/salt/README.rst:4`, `bootstrapvz/plugins/salt/README.rst:23`, `README.rst:128`, `README.rst:181`, `bootstrapvz/base/fs/volume.py:78`, `bootstrapvz/base/pkg/sourceslist.py:57`

- **Problem:** Several READMEs and links are wrong or out of date:
  - **vagrant:** The README says the default `provider` is `libvirt`, but the code defaults to `virtualbox` in both `__init__.py:9` and `tasks.py:132`. A user who builds a qcow2 box without setting `provider`, trusting the README, gets `Virtualbox vagrant boxes support vmdk images only`.
  - **minimize_size:** `shrink` accepts `qemu-img-no-compression`, but the README does not document it.
  - **VirtualBox:** The README says the guest additions ISO installs "from main Debian repo" with `apt install virtualbox-guest-additions-iso`. That package is not in main. The single backticks also render as italics instead of code.
  - **Docker:** The README mentions minimize_size twice in a confusing way (with the typo "futher"), quotes a 2015 jessie image size, and links to Project Atomic and an old `docs.docker.com/engine/userguide/...` page.
  - **Dead or moved links:**
    - zerofree at `intgat.tigress.co.uk`
    - VMware `ws45` and `my.vmware.com`, which is also printed in the shrink.py host-dependency error message
    - `manpages.debian.org/cgi-bin/man.cgi`
    - `packages.debian.org/wheezy/...` and `wheezy-backports/...`
    - `saltstack.com` and `docs.saltstack.com`

- **Fix:**
  - Change the vagrant default to `virtualbox` in the README.
  - Document `qemu-img-no-compression`.
  - Say where the ISO actually comes from (Oracle's VirtualBox download, or Debian contrib outside stable), and use double backticks.
  - Merge the Docker intro into one sentence, drop the 2015 size claim, and point the label docs at the OCI annotation conventions (`org.opencontainers.image.*`) or the current Docker docs.
  - Replace the links with zerofree at frippery.org/uml, Broadcom's VMware Workstation page, `manpages.debian.org/<page>`, `packages.debian.org/<pkg>` (or `/stable/<pkg>`), `saltproject.io` and `docs.saltproject.io`.

- **Notes:**
  - The Docker sentence that minimize_size "is required" is accurate in context. Moby's apt settings (autoclean, languages, gzip indexes, autoremove suggests) come only from minimize_size, while `dpkg.CreateDpkgCfg` in the provider only creates the dpkg config directory. Only the duplication needs fixing.
  - Checked against the saltstack/salt README on GitHub: the Salt URLs are now `saltproject.io` and `docs.saltproject.io`.
  - Unverified: VirtualBox being absent from Debian stable since stretch, zerofree's move, `my.vmware.com` being retired after the Broadcom acquisition, and Project Atomic being retired.
  - Update the salt README together with the salt plugin repair (Cloud providers).
  - Unrelated schema bug seen while checking: in `bootstrapvz/providers/docker/manifest-schema.yml:20-21`, `patternProperties` is nested inside `labels.properties`. It therefore defines a label literally named `patternProperties`, and label values are never type-checked.

- **Effort:** small; **Severity:** low

### Fix the EC2 README `profile` example

- **Where:** `bootstrapvz/providers/ec2/README.rst:67-83`, `bootstrapvz/providers/ec2/tasks/connection.py:51-58`, `bootstrapvz/providers/ec2/manifest-schema.yml:13`

- **Problem:** The README's Profile example nests `profile: Default` under `provider.credentials`. But `GetCredentials` reads `manifest.provider.get('profile')`, and the schema defines `profile` directly under `provider`; nothing reads `credentials.profile`. The README layout passes validation, so the profile is silently ignored. Without keys in the manifest or in `AWS_ACCESS_KEY`/`AWS_SECRET_KEY`, the code calls `Session(profile_name=None)` and falls back to boto3's default chain (`AWS_PROFILE`, `[default]`, instance metadata). The build then runs with whatever credentials are active, possibly in another AWS account, or it fails with the generic "No ec2 credentials found" error.

- **Fix:**
  - Move `profile:` up one level in the example, and use a lowercase profile name such as `default`. Profile names are case-sensitive.
  - Optionally, reject `credentials.profile` with `not: {required: [profile]}` under `credentials` in `manifest-schema.yml`.

- **Notes:** Do not add `additionalProperties: false` to `credentials`. The S3 keys (`certificate`, `private-key`, `user-id`) are declared only in `manifest-schema-s3.yml`, and each schema is validated separately, so that would reject valid S3 manifests.

- **Effort:** small; **Severity:** low

### Document the ansible plugin and fix its schema typos

- **Where:** `bootstrapvz/plugins/ansible/manifest-schema.yml:12-14`, `bootstrapvz/plugins/ansible/manifest-schema.yml:27-30`, `bootstrapvz/plugins/ansible/tasks.py:62-67`, `bootstrapvz/plugins/ansible/__init__.py:17-18`, `docs/conf.py:278`

- **Problem:** `bootstrapvz/plugins/ansible/` has no `README.rst`, so `docs/conf.py` generates no page for it. Its settings (`playbook`, `extra_vars`, `tags`, `skip_tags`, `opt_flags`, `groups`) and its host requirement (`ansible-playbook` on the build host, run through the `chroot` connection) are documented nowhere. The schema also has two typos that turn off checks (confirmed with jsonschema):
  - `groups` uses `host: {type: string}` instead of `items`, so non-string entries pass. They become broken inventory sections through `'[{}]\n{}\n'.format(group, conn)`, which fail only in the `user_modification` phase.
  - `extra_vars` uses the array keyword `minItems` on an object, so `extra_vars: {}` passes.

- **Fix:**
  - Add `bootstrapvz/plugins/ansible/README.rst`. Cover the settings, the host requirements, and the `extra_vars.ansible_ssh_user` case, which adds `RemoveAnsibleSSHUserDir`.
  - Change the schema to `groups: {type: array, items: {type: string}, minItems: 1}` and `extra_vars: {type: object, minProperties: 1}`.

- **Notes:**
  - The `chroot` connection plugin ships in the `community.general` collection (unverified for current ansible packaging).
  - `chef` has no README either; add one as part of the chef repair.
  - No manifest uses the ansible plugin, so the integration tests never dry-run it. Add an example manifest as part of the coverage work.

- **Effort:** small; **Severity:** low

### Bring `manifests/README.rst` in line with the schema

- **Where:** `manifests/README.rst:155`, `manifests/README.rst:167-168`, `manifests/README.rst:184`, `manifests/README.rst:224`, `manifests/README.rst:301`, `manifests/README.rst:317`, `manifests/README.rst:328`, `bootstrapvz/base/manifest-schema.yml:52-60`, `bootstrapvz/base/manifest-schema.yml:187-194`

- **Problem:** This file is the manifest reference in the published docs (included by `docs/manifests/index.rst`), and the schema rejects several values it documents:
  - **Bootloader:** it lists `pv-grub`, but the enum is `pvgrub, grub, extlinux, none`, and `none` is not documented.
  - **Hostname:** the example `hostname: jessie x86_64` fails the pattern `^\S+$`.

  Several value lists are also stale:
  - Releases stop at stretch and sid; buster through duke are missing.
  - Backings omit `vhd` (Azure) and `folder` (Docker).
  - Filesystems omit `btrfs`.
  - Line 224 tells users to add `apt-transport-https` for HTTPS repos on every release.

  Copying a documented value gives an immediate validation error rather than a broken build.

- **Fix:**
  - Change `pv-grub` to `pvgrub` and add `none`.
  - Use a valid example hostname, for example `debian`.
  - Add buster, bullseye, bookworm, trixie, forky and duke to the release list, and keep the old releases.
  - Add `vhd`, `folder` and `btrfs`.
  - Make the HTTPS note release-specific: `apt-transport-https` only for releases before buster, `ca-certificates` always.

- **Notes:** The `mountopts` entry was already corrected. Built-in HTTPS support arrived with apt 1.5, which first shipped in buster, not stretch (unverified).

- **Effort:** small; **Severity:** low

### Reword the official manifest READMEs as historical and fix the GCE links

- **Where:** `manifests/official/ec2/README.rst:4`, `manifests/official/gce/README.rst:4`, `manifests/official/gce/README.rst:7`, `manifests/official/gce/README.rst:9-27`, `README.rst:27`, `README.rst:31`, `README.rst:35`, `docs/manifests/index.rst:8-9`

- **Problem:** These files describe the manifests as current official build inputs:
  - The EC2 README says, in the present tense, "The official Debian images for EC2 are built with bootstrap-vz".
  - The GCE README calls these "the official manifests used to build" GCE Debian images.
  - README.rst says "used for official Debian images" for EC2, GCE and Oracle.

  The folders only hold old releases. EC2 has wheezy, jessie and stretch, with `ebs-stretch-amd64-hvm.yml:4` tagged "Stretch 9.0 alpha". GCE has jessie, stretch and buster, with the note "Buster is for testing only". The GCE README is reStructuredText but uses Markdown link syntax at lines 4 and 7, so the published page shows the brackets and URLs literally. Sphinx gives no warning about this.

- **Fix:**
  - Reword both READMEs and the README.rst provider list as historical: the manifests were used for Debian's official images of those releases and are kept as references.
  - Convert the two GCE links to rst (`` `text <url>`__ ``) and mark the Debian 8/9 package notes as historical.
  - Keep the manifests. Old releases stay supported, and the integration tests dry-run them.

- **Notes:**
  - Unverified: Debian's cloud team has built the official images with the FAI-based debian-cloud-images tooling since around buster.
  - The quick-start example at `README.rst:139` uses `ebs-jessie-amd64-hvm.yml`. A current-release example belongs to the "add more examples" work.

- **Effort:** small; **Severity:** low

### Enforce the 110-character line limit

- **Where:** `tox.ini:6-7`, `pylintrc:33`, `pylintrc:62`, `pylintrc:155`, `AGENTS.md:44`, `AGENTS.md:100`, `CONTRIBUTING.rst:144-148`

- **Problem:** The flake8 section in tox.ini sets `max-line-length = 110` but ignores `E501`, the only check that uses that setting. pylintrc disables `C0301` (line-too-long), which is listed under `#TODO`. So nothing enforces the limit that AGENTS.md and CONTRIBUTING.rst say `tox -e flake8` checks. 54 lines in `bootstrapvz/` and `tests/` are longer than 110 characters, for example `bootstrapvz/plugins/ec2_launch/tasks.py:60` (149) and `bootstrapvz/base/fs/volume.py:78` (146). CONTRIBUTING.rst justifies "Ignore E501: The max line length is not 80 characters", which misreads how `max-line-length` works.

- **Fix:**
  - Remove `E501` from the flake8 ignore list.
  - Remove `C0301` from the pylint disable list and from its `#TODO` comment. `max-line-length=110` is already set at pylintrc:155.
  - In the same commit, wrap the 54 lines so that every tox env keeps passing.
  - Delete the E501 bullet from CONTRIBUTING.rst.

- **Notes:** Some of the long lines are stale URLs, for example `volume.py:78`, which the stale-links entry replaces anyway.

- **Effort:** small; **Severity:** low

## Release version

### Decide on a release version to replace 0.9.11

- **Where:** `bootstrapvz/__init__.py:3`, `pyproject.toml:49`, `docs/conf.py:56-60`, `CHANGELOG.rst:4`, `docs/developers/plugins.rst:106-130`

- **Problem:**
  - `__version__` is still `'0.9.11'`, last changed in 5cc26db (2018-02-10). Both fork tags (`v2024.31.000000`, `v2026.166.122324`) carry it. pyproject.toml reads the package version from it, so the built metadata and the Sphinx docs say 0.9.11.
  - On PyPI, `bootstrap-vz` has upstream's 0.9.0 (2014) and 0.9.11 (2017-01-23, home page andsens/bootstrap-vz, no `requires_python`). The fork's version is therefore identical to upstream's Python 2 release, even though the fork has shipped breaking changes: Python 3.13 only, `puppet` replaced by `openvox`, and remote bootstrapping removed.
  - The plugin guide tells external plugins to declare `install_requires=['bootstrap-vz >= 0.9.5']` in a `setup.py`. That cannot require the fork, and where the fork is not installed, pip satisfies it with upstream's sdist.
  - CHANGELOG headings are dates only.

- **Fix:** This is a maintainer decision. The options:
  1. **`1.0.0`, then semantic versioning.** It sorts above 0.9.11 and signals the break. Change `__version__`, add the version to CHANGELOG headings, and use `>= 1.0.0` in the plugin guide. `docs/conf.py` derives `version` and `release` automatically ("1.0"). Also decide whether future tags become `v1.x.y` or stay date-based.
  2. **A date-based version matching the existing tags** (`vYYYY.DOY.HHMMSS`, for example `2026.282.0`). It is valid under PEP 440 and sorts above 0.9.11. Either bump `__version__` in the same commit as each tag, or derive it from git tags with a tool such as setuptools-scm, which adds a build dependency and replaces `[tool.setuptools.dynamic]`. PEP 440 normalizes `2024.31.000000` to `2024.31.0`. The docs `version` becomes, for example, "2026.282".
  3. **A distinct PyPI distribution name**, combined with 1 or 2, needed only if the fork is ever published to PyPI. Change `[project] name`, run `uv lock` (the lock entry is keyed by name), and update the README install text and the plugin guide. The import package stays `bootstrapvz`, so the fork and upstream cannot be installed side by side.

  Not publishing to PyPI at all also works with option 1 or 2. In that case the plugin guide should say to install the fork from git.

- **Notes:**
  - Whichever option is chosen, also move the plugin guide example from `setup.py` and `python3 setup.py develop` to a `pyproject.toml` `[project.entry-points."bootstrapvz.plugins"]` table, since the project itself dropped `setup.py`.
  - A version bump needs no `uv.lock` change, because the editable `bootstrap-vz` entry records no version.
  - The PyPI release data was checked at `pypi.org/pypi/bootstrap-vz/json`.

- **Effort:** small; **Severity:** low

## Other confirmed findings

Confirmed by the audit, not scheduled yet.

### Accept Debian's documented `packages.security` URLs on bullseye and later

- **Where:** `bootstrapvz/common/tasks/apt.py:66-74`, `bootstrapvz/base/bootstrapinfo.py:35`, `bootstrapvz/base/manifest-schema.yml:90-92`, `manifests/README.rst:216-218`

- **Problem:** Before bullseye, `AddDefaultSources` uses `packages.security` as the full repository URL (`deb {apt_security} {system.release}/updates`). From bullseye on, it appends a path with no separator (`deb {apt_security}debian-security {system.release}-security`). So on bullseye+ the value must be the parent of `debian-security` and must end in `/`, but the README only says "The default security mirror". The URL Debian documents for bullseye+ (`http://security.debian.org/debian-security`) turns into `.../debian-securitydebian-security`. With a trailing slash it turns into `.../debian-security/debian-security`, and a mirror root without a trailing slash turns into `http://mirror.landebian-security`. All of these pass validation and fail only at `AptUpdate` (package_installation phase), after debootstrap has finished. The default value works, so only manifests that override `security` on bullseye+ are affected.

- **Fix:** Keep the existing forms working and make the bullseye+ join slash-safe: strip trailing slashes, use the value unchanged when it already ends in `/debian-security`, and otherwise append `/debian-security`. In `manifests/README.rst`, document what the value must point at for each release: the security archive root before bullseye, and either that root or its parent from bullseye on. Add a unit test that runs `AddDefaultSources` on a buster manifest and on a bookworm manifest with each URL form and checks the generated lines.

- **Notes:** `{apt_security}` is also exported as a manifest variable (`bootstrapinfo.py:38`), and manifests may use it in `packages.sources`. Normalizing inside `AddDefaultSources`, instead of rewriting `info.apt_security`, leaves that variable unchanged. The schema's `format: uri` is not enforced at all, because `Manifest.schema_validator` (`bootstrapvz/base/manifest.py:130`) calls `jsonschema.validate` without a format checker. Making `security` the full repository URL on every release, with a default per release, would be cleaner, but it breaks existing bullseye+ manifests that set the parent form. The no-slash hostname case may only produce an apt warning rather than a failure, leaving the image without a working security source (unverified). The doubled path may happen to resolve on security.debian.org itself (unverified); it does not on an ordinary mirror. Once bullseye moves to archive.debian.org (its LTS ended 2026-08-31, unverified), the same key has to be `http://archive.debian.org/debian-security/` for buster but `http://archive.debian.org/` for bullseye. The only repository manifest that sets `security` is `manifests/examples/kvm/jessie-lvm.yml`, and it uses the default value.

- **Effort:** small; **Severity:** low

### Validate partition `mode` and stop it crashing on root and swap

- **Where:** `bootstrapvz/base/manifest-schema.yml:197`, `bootstrapvz/base/manifest-schema.yml:200-212`, `bootstrapvz/common/tasks/filesystem.py:130-153`, `manifests/README.rst:314`

- **Problem:** The schema accepts any string for `mode` (`mode: {type: string}`). In the volume_mounting phase, `ChmodMountDirs` runs `int(mode_str, 8)` and `os.chmod(os.path.join(info.root, partition.name), mode)`, so a non-octal value such as `u+rwx` raises `ValueError`. On the root partition (named `root`) the path is `<workspace>/root/root`, which does not exist before debootstrap, so the call raises `FileNotFoundError`. The README lists `mode` among the root partition settings, which invites exactly this case. The `swap` block has no `additionalProperties: false`, so it also accepts `mode` and fails the same way on `<root>/swap`. All of these pass schema validation (checked with jsonschema) and crash after the volume has been created, partitioned and formatted.

- **Fix:** Add `pattern: ^[0-7]{3,4}$` to `mode` and `additionalProperties: false` to the `swap` schema. For the root partition, either chmod `info.root` itself in `ChmodMountDirs`, or reject `mode` on root and remove it from the root list in the README. Only the two KVM `*-virtio-partitions.yml` examples use `mode` (`'1777'` on `tmp`), and both stay valid.

- **Notes:** With `type: none` the root partition is a `SinglePartition`, which `has_mode` skips, so `mode` there is accepted and then silently ignored. The README should say so. An unquoted `mode: 1777` loads as an int and is already rejected by `type: string`; the README example should show the quoted form.

- **Effort:** small; **Severity:** low

### Remove the `/dev/disk/by-uuid` symlinks that `link_uuid` creates on the host

- **Where:** `bootstrapvz/base/fs/partitions/base.py:75-93`, `bootstrapvz/base/fs/partitions/base.py:121-137`, `bootstrapvz/base/fs/partitionmaps/abstract.py:106-120`

- **Problem:** `link_uuid` creates `/dev/disk/by-uuid/<uuid>` on the build host, pointing at `/dev/mapper/<loop>pN`, so that `update-grub` uses the UUID. `unlink_uuid` removes the link only `if os.path.isfile(...)`. That check follows the symlink and is always False, because the target is a block device. The target is gone by then anyway, since `AbstractPartitionMap._before_unmap` runs `kpartx -ds` before `partition.unmap()`. So every build leaves its links in the host's `/dev/disk/by-uuid` until reboot. `link_uuid` checks with `os.path.exists`, which is False for a dangling link, so `os.symlink` raises `FileExistsError` when the same UUID later appears under a different device name. That happens with a `format_command` that pins the UUID, or when `prebootstrapped` reuses an image while the earlier loop device is still busy.

- **Fix:** In `link_uuid`, use `os.path.lexists()` and replace a dangling link instead of failing. In `unlink_uuid`, use `os.path.islink()` and remove the link only if this object created it, either by tracking a flag or by checking `os.readlink(path) == self.device_path`, so that a link created by the host's own udev rules is left alone.

- **Notes:** Within one build, the unmount/unmap/remap cycle in `bootstrapvz/common/fs/__init__.py` (`unmounted()`) does not hit the error: the mapper name stays the same, so the old link resolves again. Checked locally: `os.path.isfile` is False both for a symlink to a device node and for a dangling symlink, and `os.symlink` onto a dangling link raises `FileExistsError`.

- **Effort:** small; **Severity:** low

### Make `--color=always` colorize output when stdout is not a TTY

- **Where:** `bootstrapvz/base/log.py:69-81`, `bootstrapvz/base/main.py:74-82`, `pyproject.toml:21`

- **Problem:** `setup_loggers` decides whether to colorize (always for `--color=always`, `os.isatty(2)` for `auto`) and installs `ColorFormatter` on the stderr handler. `ColorFormatter.format` then calls `termcolor.colored()` without `force_color`. termcolor (3.3.0 in `uv.lock`) makes its own decision based on `os.isatty(sys.stdout.fileno())`, `NO_COLOR`, `FORCE_COLOR` and `TERM=dumb`. So `bootstrap-vz --color=always m.yml 2>&1 | tee build.log` or `| less -R` prints no color, and neither does `--color=auto` with stderr on a terminal but stdout redirected. Reproduced: with stdout piped, `ColorFormatter().format()` on an ERROR record returns plain `'boom'`. The bug is cosmetic only.

- **Fix:** Call `colored(record.msg, color, force_color=True)`, which is safe because the formatter is only installed once color has been chosen. Raise the dependency in `pyproject.toml` to `termcolor >= 2.3.0`, the first release with the `force_color` parameter, then run `uv lock`. Alternatively, drop termcolor and emit the three ANSI color codes directly, which removes a dependency.

- **Notes:** `force_color=True` also bypasses termcolor's `NO_COLOR` and `TERM=dumb` checks, which today apply only by accident. If they should be honored, check them in the `auto` branch of `main.py`. termcolor 2.1.0 added the stdout TTY check and 2.3.0 added `force_color` (checked against termcolor's source at those tags). `ColorFormatter` overwrites `record.msg`; color codes stay out of the log file today only because the file handler is added before the console handler. The other half of the original report, a doubled `[remote]` log prefix, no longer applies: remote bootstrapping and `SourceFormatter` have been removed.

- **Effort:** small; **Severity:** low

### Disable predictable interface names in extlinux images on stretch and later

- **Where:** `bootstrapvz/common/assets/extlinux/extlinux.conf:13`, `bootstrapvz/common/assets/extlinux/extlinux.conf:18`, `bootstrapvz/common/tasks/network-configuration.yml:11-34`, `bootstrapvz/common/tasks/network.py:42-54`, `bootstrapvz/common/task_groups.py:179-187`, `bootstrapvz/common/tasks/grub.py:225-234`, `bootstrapvz/common/tasks/extlinux.py:56-86`

- **Problem:** For every release from stretch on, `ConfigureNetworkIF` writes only `auto eth0` / `iface eth0 inet dhcp`. Grub images work only because `grub.DisablePNIN` adds `net.ifnames=0 biosdevname=0`, and `get_bootloader_group` adds that task only in the grub branch. The `append` lines in the extlinux template have no such parameters, and nothing else in the code base (no udev rule, no `.link` file) restores the old names. With `bootloader: extlinux` on stretch+, a PCI NIC comes up as `ens3`/`ens4` (KVM virtio, GCE), `enp0s3` (VirtualBox) or `ens5` (EC2 Nitro). `eth0` never exists, so the image boots without network unless cloud-init happens to configure it.

- **Fix:** Add `net.ifnames=0 biosdevname=0` to both `append` lines in `extlinux.conf`. The only other release that uses this template is jessie (wheezy uses `ConfigureExtlinux`), where the parameters are harmless, so they can be added unconditionally. The alternative is to build the extlinux kernel command line from the parameter list the grub tasks fill. That would also pull in `apparmor=0`, `consoleblank=0` and the other grub parameters, which changes extlinux images beyond this bug and needs a deliberate decision.

- **Notes:** extlinux is allowed by the base schema and by the ec2, kvm, virtualbox, gce and azure provider schemas. Even so, no manifest in `manifests/` uses it on stretch+ (only the wheezy EC2 official manifests do), and `tests/system/ec2_ebs_hvm_tests.py` builds extlinux images without checking networking. Azure (`hv_netvsc`) and Xen netfront instances probably keep `eth0`, but newer systemd names Xen NICs `enX0` (unverified). Related separate entries: VirtualBox with extlinux, and KVM `console: virtual` with a non-grub bootloader, crash earlier on the missing `info.grub_config`; the same template also pins the build-time kernel version.

- **Effort:** small; **Severity:** medium

### Add `elevator=noop` only for releases whose kernel still honors it

- **Where:** `bootstrapvz/common/tasks/grub.py:247-254`, `bootstrapvz/common/task_groups.py:170`, `bootstrapvz/providers/gce/tasks/boot.py:13-18`, `bootstrapvz/providers/ec2/tasks/boot.py:63-73`

- **Problem:** `grub.SetIoScheduler` ("Set VM optimized IO scheduler") appends `elevator=noop` to every grub image, whatever the release, and the GCE `ConfigureGrub` and EC2 `ConfigurePVGrub` tasks append it again. Linux 5.0 removed the legacy single-queue block layer. Since then `elevator=` only logs "Kernel parameter elevator= does not have any effect anymore" (checked in `block/elevator.c` at v5.10 and v6.12), and blk-mq calls its no-op scheduler `none`. So on bullseye (5.10), bookworm (6.1), trixie (6.12) and later, the task does nothing except add a boot warning, while the logged tasklist says the tuning was applied.

- **Fix:** In `get_bootloader_group`, add `grub.SetIoScheduler` only when `manifest.release < bullseye`, and gate the GCE and EC2 copies the same way, using the objects in `bootstrapvz/common/releases.py`. Do not delete the task: wheezy through buster are still supported, and their kernels still honor the parameter for single-queue devices. Extend `tests/unit/kernel_cmdline_tests.py` to assert that `elevator=noop` is present for buster and absent for a bookworm or trixie manifest. If the tuning is still wanted on new kernels, the replacement is a udev rule that writes `none` to `queue/scheduler`, but that is a new feature.

- **Notes:** The GCE task's `scsi_mod.use_blk_mq=Y` is dead too: the `use_blk_mq` module parameter no longer exists in `drivers/scsi/` at v5.10, so it needs the same `< bullseye` gate. The parameter was added in 2019 (commits `da58287` and `b06d9ff`).

- **Effort:** small; **Severity:** low

### Fix the Docker `labels` schema so it rejects non-string values

- **Where:** `bootstrapvz/providers/docker/manifest-schema.yml:9-21`, `bootstrapvz/providers/docker/tasks/image.py:48-50`, `bootstrapvz/providers/docker/__init__.py:36-37`

- **Problem:** `patternProperties: {^.+$: {type: string}}` is indented inside `labels.properties`, next to `distribution-scope`. JSON Schema therefore treats it as a label literally named `patternProperties`, and no other label value is type-checked. YAML loads `version: 1.0` as a float and `release: 12` as an int. A manifest with such labels passes validation (checked with jsonschema against the current schema) and the whole build runs. Then, in the image_registration phase, `PopulateLabels` calls `value.format(**info.manifest_vars)` and fails with `AttributeError: 'float' object has no attribute 'format'`. This affects Docker manifests with unquoted numeric or boolean label values.

- **Fix:** Move `patternProperties` up one level so it is a sibling of `properties` under `labels`. With that change the same manifest fails validation with "1.0 is not of type 'string'" (checked). The Docker example manifests use only string labels and stay valid. Add a unit test that validates a Docker manifest with a numeric label and expects a `ManifestError`, and note in `bootstrapvz/providers/docker/README.rst` that label values must be strings.

- **Notes:** The `dockerfile` pattern in the same schema has its own problem (see the `dockerfile` entry below); fix both together.

- **Effort:** small; **Severity:** low

### Build VirtualBox Guest Additions for the image kernel and fail when the modules are missing

- **Where:** `bootstrapvz/providers/virtualbox/tasks/guest_additions.py:47-92`, `bootstrapvz/providers/virtualbox/assets/install_guest_additions.sh:6-19`, `bootstrapvz/common/tasks/kernel.py:29-50`

- **Problem:** `InstallGuestAdditions` takes the kernel version from the first `Depends:` line of `apt-cache show linux-headers-<arch>`, which lists every available version, newest first (unverified). With a backports source, it therefore picks the bpo headers instead of the installed ones. The version is put into a bash wrapper that overrides `uname` with an exported bash function and then runs `VBoxLinuxAdditions.run --nox11`. But VirtualBox's `vboxadd.sh` is a `#! /bin/sh` script, which on Debian runs under dash, and dash ignores exported bash functions. It takes its target kernel from `TARGET_VER`, which defaults to `uname -r`. So unless the build host runs the image's kernel, the module build most likely targets the host kernel and logs "Kernel headers not found for target kernel ...". The installer runs under `log_call`, which deliberately ignores its exit code, and nothing checks for the modules afterwards, so the build reports success either way.

- **Fix:** Add `kernel.DetermineKernelVersion` to `InstallGuestAdditions.predecessors`, use `info.kernel_version`, and drop both the `apt-cache` parsing and the `uname` override. Pass the version explicitly: either export `TARGET_VER=<kernel_version>` for the installer, or afterwards run `/sbin/rcvboxadd quicksetup <kernel_version>` in the chroot with `log_check_call`. Then check that `vboxguest.ko` exists under `/lib/modules/<kernel_version>/` (VirtualBox installs it into `misc/`), and raise `TaskError` if it does not.

- **Notes:** `TARGET_VER`, `quicksetup <version>` and the `/bin/sh` shebang were checked in `src/VBox/Additions/linux/installer/vboxadd.sh` on the `main` branch of VirtualBox's GitHub repository. Older Guest Additions releases may differ (unverified), and the shell used by the makeself header was not checked. `cloud_init` and `opennebula` add backports only for wheezy, so outside wheezy the backports case needs a backports source the user added. Debian's bullseye+ kernels ship in-tree `vboxguest`/`vboxsf`, and the Guest Additions may rebuild missing modules at boot (both unverified), which would soften the effect on new releases. The four VirtualBox example manifests that set `guest_additions` target wheezy through stretch. No test executes this task (see the test coverage entry), and the system-test `apply_build_settings` mishandles `guest_additions` (separate entry).

- **Effort:** medium; **Severity:** medium

### Align the Docker `dockerfile` schema with what `docker import --change` accepts

- **Where:** `bootstrapvz/providers/docker/manifest-schema.yml:22-27`, `bootstrapvz/providers/docker/tasks/image.py:15-29`, `bootstrapvz/providers/docker/README.rst:32-34`

- **Problem:** The schema allows `^(ENTRYPOINT|CMD|USER|WORKDIR|ENV|VOLUME|EXPOSE|ONBUILD|LABEL|MAINTAINER)` and cites a 2014 Docker fork as the source. `CreateImage`, the last task of the build, passes each entry to `docker import --change`. Moby accepts only `cmd`, `entrypoint`, `healthcheck`, `env`, `expose`, `label`, `onbuild`, `stopsignal`, `user`, `volume` and `workdir` there (`validCommitCommands` in `daemon/builder/dockerfile/builder.go`, compared case-insensitively). So `- MAINTAINER foo` validates, the full tree is built, and the import then fails with "maintainer is not a valid change command". In the other direction, the schema rejects `HEALTHCHECK`, `STOPSIGNAL` and lowercase instructions that Docker accepts, and because the pattern has no word boundary, `CMDfoo` passes.

- **Fix:** Use `pattern: '^(CMD|ENTRYPOINT|ENV|EXPOSE|HEALTHCHECK|LABEL|ONBUILD|STOPSIGNAL|USER|VOLUME|WORKDIR)\s'` and point the comment at Moby's `validCommitCommands`. Keep the pattern uppercase-only and list the allowed instructions in the README. An inline `(?i)` after `^` makes Python 3.13's `re` raise "global flags not at the start of the expression", and inline flags are not part of the ECMA 262 regex dialect that JSON Schema specifies.

- **Notes:** The Moby list was checked against `moby/moby` master. The three Docker example manifests use only `CMD /bin/bash`, and the `LABEL` entries that `PopulateLabels` generates stay valid. Fix this together with the `labels` entry above.

- **Effort:** small; **Severity:** low

### Remove the unused KVM task `SetGrubSystemdShowStatus` and the duplicate task registration

- **Where:** `bootstrapvz/providers/kvm/tasks/boot.py:20-27`, `bootstrapvz/providers/kvm/__init__.py:34-42`

- **Problem:** `SetGrubSystemdShowStatus` appends `systemd.show_status=1` to `GRUB_CMDLINE_LINUX`, but no `resolve_tasks` ever adds it, and no schema option, README or other module refers to it, so it can never run. In `resolve_tasks`, `boot.SetGrubConsolOutputDeviceToVirtual` is added at line 36 and again at line 40 inside the `>= jessie` branch. The taskset is a set, so the second add does nothing and only misleads readers. Neither problem has a runtime effect.

- **Fix:** Delete `SetGrubSystemdShowStatus` and remove `boot.SetGrubConsolOutputDeviceToVirtual` from the jessie+ `taskset.update()`. If systemd status output on the virtual console is wanted, add the task in that jessie+ branch in place of the duplicate, and document it under `console` in `bootstrapvz/providers/kvm/README.rst`. The duplicate is probably a slip for exactly that.

- **Notes:** Both problems are inherited unchanged from upstream `andsens/bootstrap-vz` master. `docs/_static/graph.json`, which lists every task class, is generated by `docs/conf.py`, so it needs no edit. Related: `console: virtual` with a non-grub bootloader crashes on the missing `info.grub_config` (separate entry).

- **Effort:** small; **Severity:** low

### Make the ec2_publish manifest upload use the build credentials and reject unsupported URLs

- **Where:** `bootstrapvz/plugins/ec2_publish/tasks.py:46-73`, `bootstrapvz/plugins/ec2_publish/tasks.py:20-24`, `bootstrapvz/providers/ec2/tasks/connection.py:31-69`, `bootstrapvz/plugins/ec2_publish/manifest-schema.yml:16`, `bootstrapvz/plugins/ec2_publish/README.rst:16-18`

- **Problem:** `PublishAmiManifest` creates its client with `boto3.client('s3', region_name=region)` and passes no credentials, while `CopyAmiToRegions` and `connection.Connect` pass `info.credentials`. Those credentials come from `provider.credentials`, from bootstrap-vz's own `AWS_ACCESS_KEY`/`AWS_SECRET_KEY` variables (botocore reads `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` instead), or from `provider.profile`, and the default botocore chain sees none of them. The upload therefore raises `NoCredentialsError` or writes as a different identity. It also always sets `ACL='public-read'`, which buckets created with S3 defaults since April 2023 (ACLs disabled, Block Public Access on) reject with `AccessControlListNotSupported` or `AccessDenied` (unverified). Finally, the if/elif handles only a local path without a scheme and `*amazonaws.com` hosts that contain `s3`. `s3://bucket/amis.json`, `file:///tmp/amis.json` and any other `https://` URL match neither branch, and the task silently writes nothing. Every one of these failures happens after the AMI has been registered and any cross-region copies have started.

- **Fix:** Build the S3 client with the same `aws_access_key_id`/`aws_secret_access_key`/`aws_session_token` arguments as `CopyAmiToRegions`, through a small shared helper. Accept `s3://bucket/key`, and reject unsupported URLs at validation with a schema `pattern` on `manifest_url`, or at least raise `TaskError` in an `else` branch. For the ACL, there are two options. One is to make it opt-in through a new optional setting (schema, README, `CHANGELOG.rst`). The other is to drop it and document that the bucket policy must grant read access; that silently stops making the object public on older buckets that still allow ACLs, so it needs a `CHANGELOG.rst` entry. Add a unit test for the URL handling using `botocore.stub.Stubber`.

- **Notes:** The README documents only the legacy dash form `https://bucket.s3-region.amazonaws.com/amis.json`. For the current dot form `bucket.s3.<region>.amazonaws.com`, the region is not parsed and falls back to `us-east-1`; botocore usually follows the S3 region redirect (unverified). A relative local path is resolved against the current directory, not the manifest. The credential gap predates the boto3 port (commit `ec8bdab`), which carried it over from the boto 2 code. Related: the plugin's region enum is out of date (separate entry).

- **Effort:** medium; **Severity:** medium

### Fix cloud_init `enable_modules` insertion and validate its entries

- **Where:** `bootstrapvz/plugins/cloud_init/tasks.py:104-125`, `bootstrapvz/plugins/cloud_init/manifest-schema.yml:42-50`, `bootstrapvz/plugins/cloud_init/README.rst:14-27`, `manifests/official/ec2/ebs-jessie-amd64-hvm.yml:58-60`, `manifests/official/ec2/ebs-stretch-amd64-hvm.yml:53-55`

- **Problem:** `EnableModules` starts counting lines at the `cloud_init_modules:` header itself (the header is count 1). When `position == count`, it prints ` - <module>` before the current line. Position 1 therefore inserts above the header, into the previous section, and position 0 inserts before every line ahead of the header. PyYAML cannot parse the result in either case. The inserted line always has one space of indentation, but cloud-init 23.3 and later render list items with two (`  - seed_random`). Simulated on such a file, position 2 folds the whole list into one scalar (`'foo - migrator - seed_random ...'`), and positions 3 and higher raise `ParserError`. The build still succeeds, but cloud-init cannot read its config at boot, so the instance gets no user and no SSH keys. In the schema, `properties` sits on a `type: array` with no `items`, so entries that lack `module`/`position`, or are not objects at all, pass validation and then fail with `KeyError`/`TypeError` in system_modification. `position` is `type: number`, and `int()` silently truncates floats. The README does not document `enable_modules`.

- **Fix:** Parse the file instead of counting lines. `yaml.safe_load` the image's `etc/cloud/cloud.cfg`, insert each module into `cloud_init_modules` at the computed index, and write the result as YAML (preferably the complete `cloud_init_modules` list in a `cloud.cfg.d` drop-in). Keep the current meaning of `position` for compatibility: on a 1-space file without comment lines inside the list, position N lands at list index N-2, so the official manifests' `position: 4` puts `growpart` before the third item. Reject positions below 2, which never produced a valid file. Change the schema to `items: {type: object, properties: {module: {type: string}, position: {type: integer, minimum: 2}}, required: [module, position], additionalProperties: false}` and document the setting in the README.

- **Notes:** The upstream `config/cloud.cfg.tmpl` uses ` - migrator` at tag 23.2 and `  - ...` at tags 23.3 and 25.1.4 (checked). That Debian trixie ships cloud-init 25.1.x is unverified. The only users of `enable_modules` are the jessie and stretch EC2 manifests, and they still work because those releases ship 1-space files. A trixie or later manifest that reuses the setting writes a broken `cloud.cfg`. `SetUsername`/`SetGroups` already write the drop-in `cloud.cfg.d/02_bootstrapvz_user.cfg`; move `EnableModules` to the same mechanism. Computing the final list together with `DisableModules` avoids two edits racing on the same file. A drop-in key replaces the list from `cloud.cfg` instead of merging into it (unverified), so the drop-in must carry the full list. Only `cloud_init_modules` is accepted (`additionalProperties: false`). The same code could also allow `cloud_config_modules` and `cloud_final_modules`. `--dry-run` never executes this task, so CI does not catch it.

- **Effort:** medium; **Severity:** medium

### Derive the partition device correctly in expand_root (NVMe/MMC)

- **Where:** `bootstrapvz/plugins/expand_root/assets/expand-root:22-37`, `bootstrapvz/plugins/expand_root/tasks.py:43-55`, `bootstrapvz/plugins/expand_root/manifest-schema.yml:18-19`, `bootstrapvz/plugins/expand_root/README.rst:9-11`, `bootstrapvz/common/assets/init.d/expand-root:35`

- **Problem:** The script grows the partition with `growpart "${DEVICE}" "${PARTITION}"`. growpart takes the disk and the number separately and handles NVMe. The script then runs `resize2fs "${DEVICE}${PARTITION}"` for ext2/3/4, built by plain concatenation. With `root_device: /dev/nvme0n1, root_partition: 1` that is `/dev/nvme0n11`, which does not exist: the partition grows, the filesystem does not, and `expand-root.service` fails. Any disk whose name ends in a digit is affected (`/dev/mmcblk0`, `/dev/loop0`), because the kernel inserts `p` before the partition number for those. No manifest value works around it. `root_partition` must be an integer, and `root_device: /dev/nvme0n1p` would break growpart. The xfs branch uses `xfs_growfs /` and is not affected.

- **Fix:** Build the node with the kernel naming rule, `case "${DEVICE}" in *[0-9]) PART="${DEVICE}p${PARTITION}" ;; *) PART="${DEVICE}${PARTITION}" ;; esac`, and use `${PART}` for resize2fs and in the log messages. A more robust option resolves the root at boot: `PART=$(findmnt --noheadings --output SOURCE /)`, `DEVICE=/dev/$(lsblk --noheadings --output PKNAME "${PART}")`, `PARTITION=$(cat /sys/class/block/$(basename "${PART}")/partition)`. That also covers images whose disk name changes between machine types. With that approach, keep `root_device`/`root_partition` as optional overrides so existing manifests keep working. Document NVMe naming in the README.

- **Notes:** The official GCE manifests enable this plugin with `root_device: /dev/sda` (`manifests/official/gce/jessie.yml:44`, `stretch.yml:40`, `buster.yml:40`). On NVMe-only machine series, such as GCE C3/N4 and current EC2 instance types (unverified), even growpart would get the wrong disk, which favours the boot-time lookup. The legacy `common/assets/init.d/expand-root:35` has the same `${root_device_path}${root_index}` concatenation. Fix it together with the EC2 expand-root entry.

- **Effort:** small; **Severity:** medium

### Isolate the temporary dockerd in docker_daemon `pull_images` and accept all archive forms

- **Where:** `bootstrapvz/plugins/docker_daemon/tasks.py:105-120`, `bootstrapvz/plugins/docker_daemon/tasks.py:132-137`, `bootstrapvz/plugins/docker_daemon/__init__.py:9-17`, `bootstrapvz/plugins/docker_daemon/README.rst:27-31`

- **Problem:** `PullDockerImages` runs the image's `dockerd` with `--data-root <root>/var/lib/docker` and no `--containerd` flag. It relies on PATH to make dockerd start the image's containerd (comment at line 109). dockerd only starts a managed containerd when `/run/containerd/containerd.sock` does not exist. On a build host that runs Docker or containerd, it connects to the host's containerd instead. Docker Engine 29 uses the containerd image store by default for a fresh data root, so pulled content lands in the host's `/var/lib/containerd`. The host is polluted and the image gets nothing. Without a host containerd, the managed containerd keeps its data under `<data-root>/containerd`. The booted image's dockerd uses the system containerd at `/run/containerd/containerd.sock` (`SetDockerOpts`, line 80), backed by `/var/lib/containerd`, so the images are probably invisible at runtime as well. There are smaller gaps too:
  - Only `.tar.gz`/`.tgz` are treated as archives, so a plain `.tar` (what `docker save -o` writes) goes to `docker pull` and fails.
  - Archive paths are not resolved relative to the manifest.
  - Archive paths are not checked during validation, so a wrong path fails in system_modification, after the full bootstrap.

- **Fix:**
  - Start the image's own `containerd` from `<root>/usr/bin` with `--root <root>/var/lib/containerd`, `--state <workspace>/containerd-state`, `--address <workspace>/containerd.sock`, and `--config` pointing at an empty file in the workspace, so the host's `/etc/containerd/config.toml` is not read.
  - Pass `--containerd=<that socket>` to dockerd, and stop both daemons in the `finally` block.
  - Treat `.tar`, `.tar.gz`, `.tgz` and `.tar.xz` as archives.
  - Resolve archive paths with `rel_path(info.manifest.path, ...)`, as `file_copy` does (`file_copy/tasks.py:47-48,66`).
  - Add a `phases.validation` task, modelled on `file_copy.tasks.ValidateFiles`, that checks the archives exist.
  - Update the README.

- **Notes:** The socket behaviour was checked in moby `v28.0.0` (`cmd/dockerd/daemon.go`: `initializeContainerd` uses the system socket when `systemContainerdRunning` finds it, which is a plain `os.Lstat`). The Docker 29 release notes say the containerd image store is the default for fresh installs. The point about images being invisible at runtime is unverified. `pull_images` only became usable with the rewrite in `3d03566`, whose commit message says no real Docker build was run. No manifest in `manifests/` uses `docker_daemon`.

- **Effort:** medium; **Severity:** medium

### Accept current AWS regions in ec2_publish

- **Where:** `bootstrapvz/plugins/ec2_publish/manifest-schema.yml:12-14`, `bootstrapvz/plugins/ec2_publish/manifest-schema.yml:19-34`, `bootstrapvz/plugins/ec2_publish/tasks.py:29-35`, `bootstrapvz/providers/ec2/tasks/host.py:29`, `bootstrapvz/providers/ec2/manifest-schema-s3.yml:18`, `bootstrapvz/providers/ec2/manifest-schema-s3.yml:22-37`

- **Problem:** Every `regions` item must match a closed 13-value enum from around 2016: 11 commercial regions plus `us-gov-west-1` and `cn-north-1`. The installed botocore lists 34 commercial EC2 regions. Any of the missing ones (`us-east-2`, `eu-west-2`, `eu-west-3`, `eu-north-1`, `ap-south-1` and so on) is rejected at validation. Yet an EBS build reads its source region from instance metadata, so it can run in any of them. In the other direction, `us-gov-west-1` and `cn-north-1` belong to separate AWS partitions. `CopyAmiToRegions` calls `copy_image(SourceRegion=...)` there with the same credentials, which fails in image_registration after the whole image is built (unverified).

- **Fix:** Replace the enum with a pattern such as `^[a-z]{2}(-gov)?-[a-z]+-\d$`, which never goes stale. The alternative is a Python check against `boto3.session.Session().get_available_regions('ec2')`. It works offline from botocore data, but it covers only the `aws` partition unless `partition_name` is passed, and it lags behind new regions until boto3 is upgraded. A partition mismatch cannot be caught at manifest validation for EBS builds, because the source region is only known at runtime. Check it in a preparation task after the host metadata is read, so the build fails before bootstrapping.

- **Notes:** The S3 provider schema has the same stale enum. Handle it with the EC2 PV/S3 instance-store repair, and keep `cn-north-1`, which `manifests/official/ec2/s3-wheezy-amd64-pvm-cn-north-1.yml` uses. No manifest uses `ec2_publish`.

- **Effort:** small; **Severity:** medium

### Only adjust ec2-get-credentials in admin_user when the EC2 init scripts are installed

- **Where:** `bootstrapvz/plugins/admin_user/__init__.py:19-25`, `bootstrapvz/plugins/admin_user/tasks.py:130-140`, `bootstrapvz/providers/ec2/__init__.py:94-95`, `bootstrapvz/plugins/cloud_init/__init__.py:39`, `bootstrapvz/plugins/admin_user/README.rst:12-14`

- **Problem:** On EC2 without `pubkey`, `resolve_tasks` always adds `AdminUserPublicKeyEC2`, which runs `sed_i` on `etc/init.d/ec2-get-credentials`. That file only exists if `AddEC2InitScripts` ran. The provider skips that task when `install_init_scripts: false`, and the cloud_init plugin always discards it. In both cases `fileinput` raises `FileNotFoundError` in system_modification, after debootstrap. Every official EC2 manifest uses cloud_init, so adding `admin_user` with only `username`/`password` to one of them is enough to crash the build. The README already says the script is adjusted only "if the EC2 init scripts are installed".

- **Fix:** In `resolve_tasks`, add `AdminUserPublicKeyEC2` only when `manifest.provider.get('install_init_scripts', True)` is true and `'cloud_init' not in manifest.plugins`. Otherwise fall through to the existing "No SSH key and no password set" warning when no password is set.

- **Notes:** Do not test for `initd.AddEC2InitScripts` in the taskset. Plugins resolve in manifest order after the provider, so the result would depend on whether `cloud_init` is listed before `admin_user`. `--dry-run` does not execute `run()`, so integration tests cannot catch this.

- **Effort:** small; **Severity:** low

### Stop vagrant from overriding the root_password plugin

- **Where:** `bootstrapvz/plugins/vagrant/__init__.py:22-32`, `bootstrapvz/plugins/vagrant/tasks.py:107-114`, `bootstrapvz/plugins/root_password/__init__.py:8-13`, `bootstrapvz/plugins/root_password/tasks.py:5-18`, `bootstrapvz/base/tasklist.py:29`, `bootstrapvz/base/tasklist.py:273-301`, `manifests/examples/virtualbox/stretch-vagrant.yml:30-32`

- **Problem:** vagrant always adds its own `SetRootPassword` (`chpasswd` with `root:vagrant`), and root_password adds another `SetRootPassword`. Both are in `phases.system_modification` with no ordering between them. The topological sort orders such tasks by set/dict iteration over classes hashed by id, and that order changes between runs, so whichever runs last wins. I loaded `stretch-vagrant.yml` (`vagrant: {}` plus `root_password: {password-crypted: ...}`) in 30 fresh interpreters, and vagrant's task ran last once. That box gets root password `vagrant` instead of the configured one, while root_password still enables root SSH login and password authentication stays enabled. No warning is logged.

- **Fix:** In `vagrant.resolve_tasks`, add `tasks.SetRootPassword` only when `'root_password' not in manifest.plugins`. State in the vagrant README that root_password overrides the default `vagrant` password.

- **Notes:** The separate fix for nondeterministic task order makes the outcome reproducible, but the winner would come from a tie-break rather than intent, so this fix is still needed.

- **Effort:** small; **Severity:** medium

### Reject `zerofree` for non-ext root filesystems in minimize_size

- **Where:** `bootstrapvz/plugins/minimize_size/__init__.py:22-30`, `bootstrapvz/plugins/minimize_size/__init__.py:37-39`, `bootstrapvz/plugins/minimize_size/tasks/shrink.py:42-50`, `bootstrapvz/plugins/minimize_size/manifest-schema.yml:12-13`, `bootstrapvz/plugins/minimize_size/README.rst:36-40`

- **Problem:** `validate_manifest` only checks `shrink` against `volume.backing`. `zerofree: true` is accepted with any root filesystem, and the base schema allows `xfs` and `btrfs` (`bootstrapvz/base/manifest-schema.yml:186-187`). `Zerofree` runs `zerofree <root device>` in `phases.volume_unmounting`, and zerofree only handles ext2/3/4 (unverified, not run here). An xfs or btrfs root therefore fails at the very end of the build and rolls back.

- **Fix:** In `validate_manifest`, call `error(..., ['plugins', 'minimize_size', 'zerofree'])` when `zerofree` is true and `data['volume']['partitions']['root']['filesystem']` is not ext2, ext3 or ext4. Both partition layouts require `root`, so the key is always present. Note the restriction in the README.

- **Notes:** The two manifests that set `zerofree` (`manifests/examples/kvm/stretch-cloudimg.yml` and `buster-cloudimg.yml`) use ext4 and are not affected.

- **Effort:** small; **Severity:** low

### Round up the libvirt vagrant box `virtual_size`

- **Where:** `bootstrapvz/plugins/vagrant/tasks.py:135-138`, `bootstrapvz/common/bytes.py:44-52`, `bootstrapvz/plugins/vagrant/__init__.py:12-13`

- **Problem:** For `provider: libvirt`, `PackageBox` (image_registration) sets `virtual_size = info.volume.size.bytes.get_qty_in('G')`. `get_qty_in` raises `UnitError` unless the size is an exact multiple of 1 GiB. The volume size is the sum of the manifest's partition sizes, because gaps and headers are taken out of the partitions. A common layout such as boot `256MiB` plus root `10GiB` therefore builds completely, then fails with `Unable to convert 11005853696 bytes to a whole number in GiB` (reproduced with `MSDOSPartitionMap`), and rollback discards the volume. Validation does not check for this, and no example manifest uses vagrant with libvirt.

- **Fix:** Round up instead of requiring a whole number, for example `virtual_size = -(-int(info.volume.size.bytes) // 1024 ** 3)`. Rejecting non-GiB totals in `validate_manifest` is the alternative. Rounding up is simpler, and a slightly larger virtual size should be harmless for vagrant-libvirt (unverified).

- **Notes:** The original report also claimed that arm64 VirtualBox boxes crash in `write_ovf` (`os_info` is `None`). That path is unreachable: for arm64 the virtualbox provider already fails in the preparation phase, because `packages-kernels.yml` has no arm64 entries and `DefaultPackages` passes `None` to `info.packages.add`. That early failure belongs with the architecture/bootloader validation fix.

- **Effort:** small; **Severity:** low

### Make UnmountRoot safe to retry during rollback

- **Where:** `bootstrapvz/base/fs/partitions/abstract.py:99-107`, `bootstrapvz/base/fs/partitions/mount.py:32-40`, `bootstrapvz/common/task_groups.py:220-227`, `bootstrapvz/base/main.py:136-142`, `bootstrapvz/base/tasklist.py:34-46`

- **Problem:** `_before_unmount` unmounts each child mount first, and each `Mount.unmount()` ends with `del self.mount_dir`. Only then does it unmount the root. If any `umount` fails with "target is busy", fysom keeps the partition in `mounted` and `UnmountRoot` is not recorded as completed. The rollback map (`MountRoot: UnmountRoot`) then runs it again. The retry raises `AttributeError: 'Mount' object has no attribute 'mount_dir'` on the first child that was already unmounted, even after the busy condition has cleared. I reproduced this with the real classes and a stubbed `log_check_call`: `dev` was busy on the first attempt and nothing was busy on the retry. `TaskList.run` has no per-task error handling, so `UnmapPartitions`, `volume.Detach`, `volume.Delete` and `DeleteWorkspace` never run. The loop/nbd device, the kpartx mappings and the mounts stay on the host. Realistic triggers are a chroot process that is still exiting, or a user who frees the busy mount at the `--pause-on-error` prompt.

- **Fix:** Make unmounting idempotent. `Mount.unmount` should skip a mount that has no `mount_dir`, and, for a partition source, one whose fsm is not `mounted`. `_before_unmount` should only `umount` the root while `mount_dir` is still set. A retried `UnmountRoot` then finishes the remaining mounts.

- **Notes:** If the mount is still busy when rollback runs, an idempotent retry fails too, just with the real `umount` error instead of `AttributeError`. Do not adopt the broader options of continuing the rollback after a failing task or falling back to `umount --lazy`. They would run `kpartx -d` and `losetup -d`, and delete the image, while the volume is still mounted. A busy `dev/pts` alone already retries fine, because it is unmounted first. `DeleteWorkspace` uses `os.rmdir`, which fails safely on a non-empty workspace.

- **Effort:** small; **Severity:** low

### Keep `/usr/share/doc/*/copyright` when minimize_size excludes docs

- **Where:** `bootstrapvz/plugins/minimize_size/tasks/dpkg.py:111-125`, `bootstrapvz/plugins/minimize_size/tasks/dpkg.py:46-50`, `bootstrapvz/plugins/minimize_size/assets/bootstrap-files-filter.sh:8`, `bootstrapvz/plugins/minimize_size/README.rst:82-86`, `manifests/examples/docker/stretch-minimized.yml:12,47`, `manifests/examples/docker/jessie-minimized.yml:13,46`

- **Problem:** `ExcludeDocs` adds `/usr/share/doc/` to the debootstrap exclude list, which becomes the pattern `./usr/share/doc/.\+`. It writes only `path-exclude=/usr/share/doc/*` to `/etc/dpkg/dpkg.cfg.d/10exclude-docs`, with no `path-include` for copyright files. Packages installed by debootstrap and later by dpkg therefore lose `/usr/share/doc/<pkg>/copyright`, which Debian Policy 12.5 requires (unverified). Both docker example manifests combine `exclude_docs: true` with `distribution-scope: public`. The published image then ships GPL, BSD and MIT binaries without the licence notices those licences require on redistribution. The code comment cites Policy chapter 12 to justify dropping docs, but it misses this rule.

- **Fix:** Write `path-exclude=/usr/share/doc/*` followed by `path-include=/usr/share/doc/*/copyright` to `10exclude-docs`. The debootstrap include list is matched with `grep --invert-match --fixed-strings`, so it cannot express `*/copyright`. Add a pattern-based keep step to the filter script instead, for example appending `| grep --invert-match '^\./usr/share/doc/[^/]*/copyright$'` when docs are excluded. Say in the README that copyright files are kept.

- **Notes:** Debian's slim images (debuerreotype) and Ubuntu's minimized images keep these files (unverified). Both affected examples target EOL releases, and `exclude_docs` is opt-in. `prebootstrapped` reuses `ExcludeDocs` (`bootstrapvz/plugins/prebootstrapped/__init__.py:38`).

- **Effort:** small; **Severity:** low

### Print SSH host key fingerprints to the console, as ssh-generate-hostkeys claims

- **Where:** `bootstrapvz/common/assets/ssh-generate-hostkeys:6-23`, `bootstrapvz/common/assets/systemd/ssh-generate-hostkeys.service:12-14`, `bootstrapvz/common/tasks/ssh.py:40-55`

- **Problem:** The script says "Output the public keys to the console. This allows user to get host keys securely through console log." Every line is in fact piped to `logger -t ${prog}`, which writes only to syslog/the journal, and the unit sets no `StandardOutput`. Debian's journald does not forward to the console by default (unverified), and nothing in the repository configures that forwarding. The task is installed on EC2, Oracle and Vagrant (through `ssh_group`) and directly on KVM, GCE and Azure; only cloud_init removes it. On those images without cloud-init, the fingerprints never reach the serial log or `aws ec2 get-console-output`, so users cannot verify the host key out of band as the comment promises.

- **Fix:** Keep the `logger` calls and also write the block to the console. Either pipe it through `tee /dev/console`, or echo it to stdout and set `StandardOutput=journal+console` in the unit. If the current behaviour is preferred, correct the misleading comment instead.

- **Notes:** `/dev/console` is the last `console=` kernel argument, and that differs per provider. For example, KVM with `console: virtual` ends with `console=tty0` (`bootstrapvz/providers/kvm/tasks/boot.py:17`). Check that the output reaches the serial log each provider exposes. The wheezy and jessie init scripts (`bootstrapvz/common/assets/init.d/{wheezy,jessie}/generate-ssh-hostkeys`) use the same logger-only pattern, so this has never worked since upstream.

- **Effort:** small; **Severity:** low

## Follow-ups noted while fixing

Smaller problems found while the October 2026 fixes were made. They are not scheduled yet.

### Grub and boot

- GCE and Azure add `boot.ConfigureGrub` unconditionally, although their schemas allow
  `bootloader: extlinux`. It appends to `info.grub_config`, which only grub creates, so
  such builds crash after the bootstrap (`bootstrapvz/providers/gce/__init__.py`,
  `bootstrapvz/providers/azure/__init__.py`). VirtualBox, KVM and docker_daemon got the
  same fix already.
- VirtualBox extlinux images only get `console=ttyS0`, so boot messages and the
  recovery entry are not visible in a VM without a serial port. The KVM README sentence
  about kernel messages going to both consoles is inaccurate, and
  `docs/supported_builds.rst` still lists KVM as TODO.
- extlinux images get no `cgroup_enable=memory` from docker_daemon, because the
  extlinux template has no hook for extra kernel parameters.
- extlinux kernel symlinks: a kernel installed through `bootstrapper.include_packages`,
  or already present in a prebootstrapped image, gets its links in `/` instead of
  `/boot`, so the extlinux entry does not find it. A `vmlinuz.old` entry would give a
  fallback after a bad kernel update. `boot.txt` is copied but never shown and its text
  is truncated. `DetermineKernelVersion` sorts kernel versions as strings
  (`bootstrapvz/common/tasks/kernel.py`).
- kvm `SetSystemdTTYVTDisallocate` uses `os.mkdir`, which fails when the drop-in
  directory already exists.
- kvm `VirtIO` fails with `FileNotFoundError` if a future release defaults to dracut and
  the image has no `etc/initramfs-tools` (speculative).

### Partitions and volumes

- Partition sizes that are not whole MiB (the schema allows for example `1536KiB`) still
  leave the following partitions unaligned. GPT volumes leave one sector unused at the
  end (`pad_end` could be 33 instead of 34).
- The partition table schema accepts additional partitions (for example `home`) for both
  table types. GPT creates, mounts and lists them in fstab, but `MSDOSPartitionMap` raises
  `PartitionError` for them when the volume is loaded, after validation passed. Reject
  them for msdos during validation.
- The EC2 S3 path (`S3FStab` in `bootstrapvz/providers/ec2/tasks/filesystem.py`) writes
  `defaults` for the root in fstab and ignores `mountopts`.

### Host isolation

- `ClearMachineId` opens `var/lib/dbus/machine-id` from outside the chroot. If dbus made
  it an absolute symlink to `/etc/machine-id` (unverified), this truncates the build
  host's machine-id.
- The file_copy plugin writes to `root + dst` from outside the chroot, so a destination
  under an absolute symlink in the image (for example `/var/run`) resolves on the host.
- debootstrap still inherits the host's `no_proxy`, `https_proxy` and wgetrc, which can
  bypass the `apt_proxy` setting.

### Logging and credentials

- Log files are created with the process umask, usually 0644 in a 0755
  `/var/log/bootstrap-vz`, so every local user can read them.
- `packages.apt.conf.d` values are validated with `apt-config` and logged as stdin
  (`bootstrapvz/base/__init__.py`), including any proxy credentials in them.
- The EC2 S3 path passes `--access-key` and `--secret-key` to `euca-upload-bundle` on
  the command line (`bootstrapvz/providers/ec2/tasks/ami.py`), so the long-term secret
  key is logged and visible in the process list. Pass it through the environment.
- apt_proxy puts the username and password into the proxy URL without URL encoding, and
  `CheckAptProxy` probes the proxy without credentials.

### Packages and plugins

- The GCE provider still adds `ntp` and `isc-dhcp-client` unconditionally
  (`bootstrapvz/providers/gce/tasks/packages.py:21,23`). ntpsec dropped its transitional
  `ntp` package in 1.2.3+dfsg1-4, so GCE builds for forky and sid probably fail at package
  installation; trixie still has the old transitional package. isc-dhcp-client is likely
  gone after trixie too. Install `ntpsec` from bookworm on, as the ntp plugin does.
- cloud_init: the user drop-in gets its mode from the host umask (the plugin's other
  drop-in is chmod 0644), `debian_cloud.cfg` still uses the deprecated
  `apt_preserve_sources_list`, and `SetCloudInitMountOptions` is misnamed (it copies
  `01_debian_cloud.cfg`, which sets no mount options).
- prebootstrapped: a volume restored from a snapshot gets no manifest tags and no
  encryption, and a failing `volume_available` waiter leaks the created volume (the same
  as in `ebs.Create`).
- minbase without netbase: openvox `ApplyManifest` leaves an empty `/etc/hosts`, a
  `system.hostname` gets no `127.0.1.1` entry, and the netbase exception for
  `ConfigureNetworkIF` is questionable because `/etc/network` comes from ifupdown.
- VirtualBox and Docker manifests that install openssh-server through
  `packages.install` still ship the build-time SSH host keys, because only some
  providers schedule `ShredHostkeys`. `AddSSHKeyGeneration` decides inside `run()`
  whether openssh-server is installed.

### Init scripts and task ordering

- `RemoveHWClock` masks `hwclock.sh.service`, but systemd-sysv-generator names the unit
  `hwclock.service`, so the mask never applied.
- `InstallInitScripts` needs a `<script>.service` next to every script on stretch and
  newer. A third-party plugin without one fails with a bare `FileNotFoundError`; the
  convention is not documented.
- The native init script units are oneshot without a start timeout, so a hanging
  user-data script or growpart now blocks `multi-user.target` instead of being stopped
  after 5 minutes.
- Some tasks in the same phase touch related state without an ordering constraint, for
  example openvox `ApplyManifest` against the timezone, network and admin_user tasks.
  The order is deterministic now, but explicit predecessors may be needed.
  `strongly_connected_components` in `bootstrapvz/base/tasklist.py` is recursive and
  logs cycle members in set order.
- debootstrap `--make-tarball` treats exit status 1 as success, which hides the first
  error (`MakeTarball` in `bootstrapvz/common/tasks/bootstrap.py`). `gpgv` is not
  registered as a host dependency, and the default mirrors are still plain HTTP (the
  signatures are checked now).

### Architectures and tests

- Building arm64 images on an amd64 host is not supported (no `--foreign` or
  qemu-user-static setup) and not documented. GCE and EC2 have no arm64 kernels or UEFI
  boot path.
- `tests/system/manifests/__init__.py` only globs `*.yml` and `*.json`, and its two glob
  variables have swapped names.

## Bugs found by the new tests

Found while the task body, plugin and release matrix tests and the bookworm and trixie
examples were written in October 2026. The tests leave these cases out until they are
fixed.

### Validation and schemas

- debconf: `bootstrapvz/plugins/debconf/manifest-schema.yml:1` declares
  `$schema: http://json-schema.org/schema#`, so jsonschema warns that the metaschema is
  unknown and will raise an error in a future version. Use draft-04 like the other schemas.
- debconf: `validate_manifest` (`bootstrapvz/plugins/debconf/__init__.py:5-6`) runs
  `debconf-set-selections --checkonly` through `log_check_call`, so malformed selections
  raise `CalledProcessError` instead of a `ManifestError` at `plugins.debconf`, and even a
  `--dry-run` needs debconf-set-selections on the host. Use `log_call` and `error()`.
- file_copy: `mkdirs` has an `items` schema but no `type: array`
  (`bootstrapvz/plugins/file_copy/manifest-schema.yml:8-9`), so `mkdirs: {dir: /srv}`
  validates and `MkdirCommand` fails with `TypeError`.
- salt: `minItems: 1` on the `grains` object has no effect
  (`bootstrapvz/plugins/salt/manifest-schema.yml:16`), so `grains: {}` validates.
- prebootstrapped: the `volume.backing` enum leaves out qcow2, vhd and lvm
  (`bootstrapvz/plugins/prebootstrapped/manifest-schema.yml:9-16`), so for example
  `manifests/examples/kvm/buster-cloudimg.yml` cannot use the plugin. qcow2 and vhd probably
  work like vdi and vmdk (unverified).
- Docker validation accepts arm64 on wheezy, although Debian's arm64 port starts with
  jessie, so debootstrap would fail. The docker provider has no kernel table to reject it.

### Common tasks

- `GenerateLocale` (`bootstrapvz/common/tasks/locale.py:24-36`) only finds locale.gen
  entries of the form `<locale>.<charmap> <charmap>`. For `locale: en_US` with
  `charmap: ISO-8859-1` it searches for `# en_US.ISO-8859-1 ISO-8859-1` and fails with
  `UnexpectedNumMatchesError` if locale.gen lists `# en_US ISO-8859-1` (the exact Debian
  line is unverified), and it would set `LANG=en_US.ISO-8859-1`.
- `apt.WriteConfiguration` (`bootstrapvz/common/tasks/apt.py:129`) sets `decription`
  instead of `description`, so the build log shows its module path.
- `CleanTMP` (`bootstrapvz/common/tasks/cleanup.py:13-17`) calls `shutil.rmtree` on every
  entry of the image's /tmp that is not a regular file, which fails on a symlink, and it
  fails when `var/log/bootstrap.log` or `dpkg.log` is missing.
- `InstallPackages.install_local` (`bootstrapvz/common/tasks/packages.py:75-96`) copies
  local packages to `/tmp/<basename>`, so two .debs with the same file name from
  different directories overwrite each other.
- `InstallTrustedKeys` keeps the key's file name, but apt only reads `*.gpg` and `*.asc`
  in trusted.gpg.d (unverified), so a key with another extension is silently ignored.
- `RemoveDNSInfo` and `RemoveHostname` (`bootstrapvz/common/tasks/network.py:10-25`)
  decide with `os.path.isfile`, which follows symlinks from outside the chroot.
- `DisableSSHDNSLookup` (`bootstrapvz/common/tasks/ssh.py:118`) appends `UseDNS no`
  without a trailing newline.
- `get_fs_specific_group` (`bootstrapvz/common/task_groups.py:201-213`) only looks at the
  boot and root filesystems. An xfs `/var` gets `mkfs.xfs` on the host but no xfsprogs
  in the image, although fstab checks it, and ext partitions get no `TuneVolumeFS` when
  root is xfs.

### Providers

- EC2 pvgrub: `UpdateGrubConfig` declares `successors = [grub.WriteGrubConfig]`
  (`bootstrapvz/providers/ec2/tasks/boot.py:22`), so update-grub runs before
  /etc/default/grub is written and pv-grub's menu.lst lacks `console=hvc0`,
  `consoleblank=0`, the timeout and the other settings. Make it a predecessor, and give
  `ConfigurePVGrub` (line 66) `successors = [grub.WriteGrubConfig]`. Part of the PV path
  repair.
- EC2 pvgrub with a separate boot partition: `CreatePVGrubCustomRule` writes
  `root (hd0,<root index - 1>)` while 40_custom makes kernel paths relative to /boot, so
  the entry may point at the wrong partition (unverified, no manifest uses this layout).
- Dead code: `AddBuildEssentialPackage` in `bootstrapvz/providers/ec2/tasks/network.py:31`
  is not used anywhere.
- The official GCE manifests install python-google-compute-engine and
  python3-google-compute-engine, which the current google-guest-agent conflicts with
  (unverified whether the old per-release suites still resolve this).

### Plugins

- vagrant: `write_ovf` (`bootstrapvz/plugins/vagrant/tasks.py:221-226`) sets namespaced
  `ovf:uuid`, `ovf:name`, `ovf:lastStateChange` and `ovf:MACAddress` attributes, but
  `assets/box.ovf` uses unprefixed attributes, so the box keeps the placeholders
  `{[SYSTEM_UUID]}`, `[BOXNAME]`, `[LAST_CHANGED]` and `[MAC_ADDRESS]` and gains `ns0:*`
  attributes. The machine `OSType` is always `Debian_64`, also for i386.
- openvox: `ApplyManifest` removes its temporary hosts line with the unanchored pattern
  `127.0.0.1\s*{hostname}\n?` (`bootstrapvz/plugins/openvox/tasks.py:92,102`). With the
  hostname `localhost` it also matches netbase's `127.0.0.1 localhost` line, so `sed_i`
  raises `UnexpectedNumMatchesError`.
- minimize_size: with `dpkg: {exclude_docs: true}` and no `locales`, the include list is
  empty and `assets/bootstrap-files-filter.sh:8` runs `grep --invert-match
  --fixed-strings ''`, which drops every line, so debootstrap extracts /usr/share/doc
  after all (`bootstrapvz/plugins/minimize_size/tasks/dpkg.py:48-50`).
- minimize_size: `path-include=/usr/share/man/man[1-9]`
  (`bootstrapvz/plugins/minimize_size/tasks/dpkg.py:96`) probably only matches the
  directories, because dpkg matches with fnmatch, so English man pages of later packages
  are dropped. `/usr/share/man/man[1-9]/*` likely fixes it (unverified with real dpkg).
- expand_root on jessie: `InstallGrowpart` installs cloud-guest-utils from
  jessie-backports, but `resolve_tasks` never adds `apt.AddBackports`, so kvm and
  virtualbox jessie builds fail with `PackageError` (`bootstrapvz/plugins/expand_root`).
  Add the backports source for jessie, as cloud_init does for wheezy.
- apt_proxy: `CheckAptProxy` (`bootstrapvz/plugins/apt_proxy/tasks.py:31-45`) only catches
  `URLError`. A proxy that accepts the connection but never answers raises
  `TimeoutError`, so validation ends in a traceback instead of the warning. Catch
  `OSError`.
- salt: `BootstrapSaltMinion` leaves `/install_salt.sh` in the image.
- chef: the `assets` path is used as given, so a relative path resolves against the
  current directory, while ansible, admin_user and file_copy resolve paths against the
  manifest.
- file_copy: directory sources use `shutil.copytree`, which raises `FileExistsError` when
  the destination already exists in the image (for example a directory copied to
  /etc/ssh).
- Long options and `$PATH` lookup (AGENTS.md) are not used by google_cloud_repo
  (`wget -O`), minimize_size (`/usr/bin/vmware-vdiskmanager -k`) and prebootstrapped
  (`cp -a`).

### Docs

- The README.rst quick starts for Docker and VirtualBox Vagrant still use jessie
  examples, and the Docker provider README still claims an 82 MB image. Point them at the
  new trixie examples.
- The admin_user README says the plugin disables SSH root login, but it only does so
  before jessie.
- The debconf README example uses a folded scalar (`debconf: >-`), which joins the
  selections into one line. Use `|`.
- google_cloud_repo does not match Google's current setup: Google documents
  `/etc/apt/keyrings/google-keyring.gpg` with `signed-by`, gce-configs-trixie replaces the
  keyring package that `enable_keyring_repo` installs, and `cleanup_bootstrap_key` only
  works together with `enable_keyring_repo`.
