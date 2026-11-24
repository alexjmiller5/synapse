self:
{ config, lib, pkgs, ... }:
let
  cfg = config.services.synapse-notes-intake;
  exe = lib.getExe' cfg.package "notes-intake";
  environment = {
    HOME = "/Users/${cfg.user}";
    NOTES_INTAKE_STATE_DIR = cfg.stateDir;
  } // lib.optionalAttrs (cfg.credentialCommand != []) {
    NOTES_INTAKE_TOKEN_COMMAND = builtins.toJSON cfg.credentialCommand;
  };
in {
  options.services.synapse-notes-intake = {
    enable = lib.mkEnableOption "conservative Apple Notes inbox capture into Synapse";
    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.notes-intake;
      description = "Installed intake client package.";
    };
    user = lib.mkOption {
      type = lib.types.str;
      description = "Login user whose Notes and Keychain are available.";
    };
    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/Users/${cfg.user}/.local/state/synapse-notes-intake";
      description = "Private app state, policy, journal and log directory.";
    };
    intervalSeconds = lib.mkOption {
      type = lib.types.ints.positive;
      default = 300;
      description = "Seconds between checks; native reader also requires fifteen quiet minutes.";
    };
    credentialCommand = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [];
      description = "Optional argv printing this client's credential; empty uses its native Keychain enrollment.";
    };
  };
  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ (pkgs.writeShellScriptBin "notes-intake" ''
      export NOTES_INTAKE_STATE_DIR=${lib.escapeShellArg cfg.stateDir}
      exec ${exe} "$@"
    '') ];
    system.activationScripts.postActivation.text = lib.mkAfter ''
      /usr/bin/sudo -u ${lib.escapeShellArg cfg.user} /bin/mkdir -p ${lib.escapeShellArg cfg.stateDir}
      /bin/chmod 700 ${lib.escapeShellArg cfg.stateDir}
    '';
    launchd.user.agents.synapse-notes-intake.serviceConfig = {
      Label = "org.synapse.notes-intake";
      ProgramArguments = [ exe "once" ];
      EnvironmentVariables = environment;
      StartInterval = cfg.intervalSeconds;
      RunAtLoad = true;
      ProcessType = "Background";
      StandardOutPath = "${cfg.stateDir}/intake.log";
      StandardErrorPath = "${cfg.stateDir}/intake.log";
    };
  };
}
