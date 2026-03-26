#!/usr/bin/env python3
"""
CFCat - CloudFormation Catalog Tool
Reads CFN templates and writes a structured, LLM-friendly infrastructure summary.

Usage:
    cfcat template.yaml
    cfcat ./infra/
    cfcat ./infra/ ./shared/ -o my-infra.txt
"""

import sys
import json
import os
import argparse
from pathlib import Path

VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# YAML support (optional dependency — graceful degradation to JSON-only)
# ---------------------------------------------------------------------------
try:
    import yaml

    # CFN uses shorthand YAML tags (!Ref, !GetAtt, !Sub, etc.) that safe_load
    # doesn't know about. Register constructors that convert them to the same
    # dict form that JSON CFN templates use, so the rest of the tool works
    # identically regardless of input format.
    def _cfn_tag_constructor(tag_suffix):
        """Return a constructor that wraps scalar/seq/map values in {tag: val}."""
        fn_name = tag_suffix  # e.g. "Ref", "Fn::Sub"

        def constructor(loader, node):
            if isinstance(node, yaml.ScalarNode):
                val = loader.construct_scalar(node)
                # !GetAtt Resource.Attribute -> {"Fn::GetAtt": ["Resource", "Attribute"]}
                if fn_name == "Fn::GetAtt" and isinstance(val, str) and "." in val:
                    parts = val.split(".", 1)
                    return {"Fn::GetAtt": parts}
                return {fn_name: val}
            elif isinstance(node, yaml.SequenceNode):
                return {fn_name: loader.construct_sequence(node, deep=True)}
            elif isinstance(node, yaml.MappingNode):
                return {fn_name: loader.construct_mapping(node, deep=True)}
        return constructor

    # Map of shorthand tag -> canonical function name
    _CFN_TAGS = {
        "!Ref":          "Ref",
        "!GetAtt":       "Fn::GetAtt",
        "!Sub":          "Fn::Sub",
        "!ImportValue":  "Fn::ImportValue",
        "!Join":         "Fn::Join",
        "!Select":       "Fn::Select",
        "!Split":        "Fn::Split",
        "!If":           "Fn::If",
        "!FindInMap":    "Fn::FindInMap",
        "!Base64":       "Fn::Base64",
        "!Equals":       "Fn::Equals",
        "!Not":          "Fn::Not",
        "!And":          "Fn::And",
        "!Or":           "Fn::Or",
        "!Cidr":         "Fn::Cidr",
        "!Transform":    "Fn::Transform",
        "!Condition":    "Condition",
        "!GetAZs":       "Fn::GetAZs",
        "!ValueOf":      "Fn::ValueOf",
        "!ValueOfAll":   "Fn::ValueOfAll",
    }

    class CfnLoader(yaml.SafeLoader):
        pass

    for _tag, _fn in _CFN_TAGS.items():
        CfnLoader.add_constructor(_tag, _cfn_tag_constructor(_fn))

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    CfnLoader = None

# ---------------------------------------------------------------------------
# Key properties to surface per resource type (all others are omitted unless
# they contain cross-stack references, which are always surfaced)
# ---------------------------------------------------------------------------
KEY_PROPERTIES = {
    "AWS::EC2::VPC":                            ["CidrBlock", "EnableDnsSupport", "EnableDnsHostnames", "InstanceTenancy"],
    "AWS::EC2::Subnet":                         ["CidrBlock", "VpcId", "AvailabilityZone", "MapPublicIpOnLaunch"],
    "AWS::EC2::SecurityGroup":                  ["GroupDescription", "GroupName", "VpcId"],
    "AWS::EC2::SecurityGroupIngress":           ["GroupId", "IpProtocol", "FromPort", "ToPort", "CidrIp", "CidrIpv6", "SourceSecurityGroupId"],
    "AWS::EC2::SecurityGroupEgress":            ["GroupId", "IpProtocol", "FromPort", "ToPort", "CidrIp", "CidrIpv6"],
    "AWS::EC2::Instance":                       ["InstanceType", "ImageId", "KeyName", "SubnetId", "IamInstanceProfile"],
    "AWS::EC2::InternetGateway":                ["Tags"],
    "AWS::EC2::VPCGatewayAttachment":           ["VpcId", "InternetGatewayId"],
    "AWS::EC2::RouteTable":                     ["VpcId"],
    "AWS::EC2::Route":                          ["RouteTableId", "DestinationCidrBlock", "GatewayId", "NatGatewayId", "TransitGatewayId"],
    "AWS::EC2::SubnetRouteTableAssociation":    ["SubnetId", "RouteTableId"],
    "AWS::EC2::NatGateway":                     ["SubnetId", "AllocationId", "ConnectivityType"],
    "AWS::EC2::EIP":                            ["Domain"],
    "AWS::EC2::TransitGateway":                 ["Description", "AmazonSideAsn"],
    "AWS::EC2::TransitGatewayAttachment":       ["TransitGatewayId", "VpcId", "SubnetIds"],
    "AWS::S3::Bucket":                          ["BucketName", "AccessControl", "VersioningConfiguration", "BucketEncryption", "PublicAccessBlockConfiguration"],
    "AWS::S3::BucketPolicy":                    ["Bucket"],
    "AWS::Lambda::Function":                    ["FunctionName", "Runtime", "Handler", "Role", "MemorySize", "Timeout", "Environment", "Layers"],
    "AWS::Lambda::LayerVersion":                ["LayerName", "CompatibleRuntimes"],
    "AWS::Lambda::EventSourceMapping":          ["EventSourceArn", "FunctionName", "StartingPosition", "BatchSize"],
    "AWS::Lambda::Permission":                  ["FunctionName", "Action", "Principal", "SourceArn"],
    "AWS::IAM::Role":                           ["RoleName", "Path", "ManagedPolicyArns", "MaxSessionDuration"],
    "AWS::IAM::Policy":                         ["PolicyName", "Roles", "Users", "Groups"],
    "AWS::IAM::ManagedPolicy":                  ["ManagedPolicyName", "Path", "Roles"],
    "AWS::IAM::InstanceProfile":                ["InstanceProfileName", "Roles"],
    "AWS::RDS::DBInstance":                     ["DBInstanceIdentifier", "DBInstanceClass", "Engine", "EngineVersion", "MultiAZ", "DBName", "StorageType", "AllocatedStorage"],
    "AWS::RDS::DBCluster":                      ["DBClusterIdentifier", "Engine", "EngineVersion", "DatabaseName", "DeletionProtection"],
    "AWS::RDS::DBSubnetGroup":                  ["DBSubnetGroupDescription", "SubnetIds"],
    "AWS::ECS::Cluster":                        ["ClusterName", "CapacityProviders"],
    "AWS::ECS::Service":                        ["ServiceName", "Cluster", "TaskDefinition", "DesiredCount", "LaunchType", "NetworkConfiguration"],
    "AWS::ECS::TaskDefinition":                 ["Family", "Cpu", "Memory", "NetworkMode", "RequiresCompatibilities", "ExecutionRoleArn", "TaskRoleArn"],
    "AWS::EKS::Cluster":                        ["Name", "Version", "RoleArn"],
    "AWS::EKS::Nodegroup":                      ["ClusterName", "NodegroupName", "NodeRole", "Subnets", "InstanceTypes", "ScalingConfig"],
    "AWS::ElasticLoadBalancingV2::LoadBalancer": ["Name", "Type", "Scheme", "Subnets", "IpAddressType"],
    "AWS::ElasticLoadBalancingV2::Listener":    ["LoadBalancerArn", "Protocol", "Port", "DefaultActions"],
    "AWS::ElasticLoadBalancingV2::TargetGroup": ["Name", "Protocol", "Port", "TargetType", "VpcId"],
    "AWS::CloudFront::Distribution":            ["DistributionConfig"],
    "AWS::CloudFront::CloudFrontOriginAccessIdentity": ["CloudFrontOriginAccessIdentityConfig"],
    "AWS::DynamoDB::Table":                     ["TableName", "BillingMode", "KeySchema", "GlobalSecondaryIndexes"],
    "AWS::SNS::Topic":                          ["TopicName", "DisplayName", "FifoTopic"],
    "AWS::SNS::Subscription":                  ["TopicArn", "Protocol", "Endpoint"],
    "AWS::SQS::Queue":                          ["QueueName", "VisibilityTimeout", "MessageRetentionPeriod", "FifoQueue", "RedrivePolicy"],
    "AWS::ApiGateway::RestApi":                 ["Name", "Description", "EndpointConfiguration"],
    "AWS::ApiGateway::Stage":                   ["RestApiId", "StageName", "DeploymentId"],
    "AWS::ApiGatewayV2::Api":                   ["Name", "ProtocolType", "CorsConfiguration"],
    "AWS::ApiGatewayV2::Stage":                 ["ApiId", "StageName", "AutoDeploy"],
    "AWS::CloudFormation::Stack":               ["TemplateURL", "Parameters"],
    "AWS::SecretsManager::Secret":              ["Name", "Description", "KmsKeyId"],
    "AWS::SSM::Parameter":                      ["Name", "Type", "Tier"],
    "AWS::KMS::Key":                            ["Description", "EnableKeyRotation", "KeyUsage"],
    "AWS::KMS::Alias":                          ["AliasName", "TargetKeyId"],
    "AWS::Cognito::UserPool":                   ["UserPoolName", "MfaConfiguration", "AutoVerifiedAttributes"],
    "AWS::Cognito::UserPoolClient":             ["UserPoolId", "ClientName", "AllowedOAuthFlows"],
    "AWS::Events::Rule":                        ["Name", "Description", "ScheduleExpression", "EventPattern", "State"],
    "AWS::StepFunctions::StateMachine":         ["StateMachineName", "StateMachineType", "RoleArn"],
    "AWS::ElastiCache::ReplicationGroup":       ["ReplicationGroupDescription", "CacheNodeType", "Engine", "NumCacheClusters"],
    "AWS::ElastiCache::SubnetGroup":            ["CacheSubnetGroupName", "SubnetIds"],
    "AWS::MSK::Cluster":                        ["ClusterName", "KafkaVersion", "NumberOfBrokerNodes"],
    "AWS::Kinesis::Stream":                     ["Name", "ShardCount", "StreamEncryption"],
    "AWS::Glue::Job":                           ["Name", "Command", "Role", "GlueVersion"],
    "AWS::Glue::Database":                      ["CatalogId", "DatabaseInput"],
    "AWS::Glue::Crawler":                       ["Name", "Role", "DatabaseName"],
    "AWS::CodePipeline::Pipeline":              ["Name", "RoleArn"],
    "AWS::CodeBuild::Project":                  ["Name", "ServiceRole", "Environment"],
    "AWS::CloudWatch::Alarm":                   ["AlarmName", "MetricName", "Namespace", "Statistic", "Threshold", "ComparisonOperator"],
    "AWS::Logs::LogGroup":                      ["LogGroupName", "RetentionInDays"],
    "AWS::WAFv2::WebACL":                       ["Name", "Scope", "DefaultAction"],
    "AWS::Route53::RecordSet":                  ["HostedZoneId", "Name", "Type", "AliasTarget"],
    "AWS::Route53::HostedZone":                 ["Name"],
    "AWS::ACM::Certificate":                    ["DomainName", "ValidationMethod", "SubjectAlternativeNames"],
}

# Values under these keys are always truncated — they're verbose and rarely
# useful at a summary level
TRUNCATE_KEYS = {
    "PolicyDocument", "AssumeRolePolicyDocument", "UserData", "ZipFile",
    "TemplateBody", "InlineCode", "DefinitionString", "Definition",
}

MAX_VALUE_LEN = 120  # truncate any rendered value longer than this

# ---------------------------------------------------------------------------
# Intrinsic function renderer
# ---------------------------------------------------------------------------

def render_value(v, depth=0):
    """
    Render a CFN property value as a human-readable string.
    Handles intrinsic functions, lists, dicts, and scalars.
    """
    if depth > 4:
        return "..."

    if v is None:
        return "null"

    if isinstance(v, bool):
        return str(v).lower()

    if isinstance(v, (int, float)):
        return str(v)

    if isinstance(v, str):
        return _truncate(v)

    if isinstance(v, list):
        if len(v) == 0:
            return "[]"
        items = [render_value(i, depth + 1) for i in v]
        if len(items) <= 3:
            return "[" + ", ".join(items) + "]"
        return "[" + ", ".join(items[:3]) + f", ...+{len(items)-3} more]"

    if isinstance(v, dict):
        # Intrinsic functions
        if "Ref" in v and len(v) == 1:
            return f"!Ref {v['Ref']}"
        if "Fn::GetAtt" in v and len(v) == 1:
            parts = v["Fn::GetAtt"]
            if isinstance(parts, list) and len(parts) == 2:
                return f"!GetAtt {parts[0]}.{parts[1]}"
            return f"!GetAtt {parts}"
        if "Fn::Sub" in v and len(v) == 1:
            sub = v["Fn::Sub"]
            if isinstance(sub, list):
                return f"!Sub \"{sub[0]}\""
            return f"!Sub \"{_truncate(str(sub), 80)}\""
        if "Fn::ImportValue" in v and len(v) == 1:
            return f"!ImportValue {render_value(v['Fn::ImportValue'], depth+1)}"
        if "Fn::Join" in v and len(v) == 1:
            args = v["Fn::Join"]
            if isinstance(args, list) and len(args) == 2:
                delim, parts = args
                rendered = render_value(parts, depth + 1)
                return f"!Join [{repr(delim)}, {rendered}]"
        if "Fn::Select" in v and len(v) == 1:
            args = v["Fn::Select"]
            if isinstance(args, list) and len(args) == 2:
                return f"!Select [{args[0]}, {render_value(args[1], depth+1)}]"
        if "Fn::Split" in v and len(v) == 1:
            args = v["Fn::Split"]
            return f"!Split {render_value(args, depth+1)}"
        if "Fn::If" in v and len(v) == 1:
            args = v["Fn::If"]
            return f"!If [{args[0]}, ...]" if isinstance(args, list) else "!If ..."
        if "Fn::FindInMap" in v and len(v) == 1:
            args = v["Fn::FindInMap"]
            return f"!FindInMap {render_value(args, depth+1)}"
        if "Condition" in v and len(v) == 1:
            return f"!Condition {v['Condition']}"

        # Generic dict — render as key: value pairs (abbreviated)
        pairs = []
        for k, val in list(v.items())[:4]:
            pairs.append(f"{k}: {render_value(val, depth+1)}")
        result = "{" + ", ".join(pairs) + ("}" if len(v) <= 4 else f", ...+{len(v)-4} more}}")
        return _truncate(result)

    return _truncate(str(v))


def _truncate(s, limit=MAX_VALUE_LEN):
    s = str(s)
    if len(s) > limit:
        return s[:limit] + " [...]"
    return s


# ---------------------------------------------------------------------------
# Cross-stack reference collectors
# ---------------------------------------------------------------------------

def collect_import_values(obj, found=None):
    """Recursively walk a CFN object and collect rendered Fn::ImportValue strings."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        if "Fn::ImportValue" in obj:
            found.append(render_value(obj["Fn::ImportValue"]))
        else:
            for v in obj.values():
                collect_import_values(v, found)
    elif isinstance(obj, list):
        for item in obj:
            collect_import_values(item, found)
    return found


def collect_raw_imports(obj, found=None):
    """
    Like collect_import_values but returns the raw (unrendered) argument to
    Fn::ImportValue. Used for dependency matching so we compare apples to apples
    with the export index (which is also built from raw values, then rendered).
    """
    if found is None:
        found = []
    if isinstance(obj, dict):
        if "Fn::ImportValue" in obj:
            found.append(obj["Fn::ImportValue"])
        else:
            for v in obj.values():
                collect_raw_imports(v, found)
    elif isinstance(obj, list):
        for item in obj:
            collect_raw_imports(item, found)
    return found


def collect_exports(outputs):
    """Return list of 'output_key -> rendered_export_name' strings for display."""
    exports = []
    for name, val in (outputs or {}).items():
        if isinstance(val, dict) and "Export" in val:
            export = val["Export"]
            if isinstance(export, dict) and "Name" in export:
                exports.append(f"{name} -> {render_value(export['Name'])}")
            else:
                exports.append(name)
    return exports


# ---------------------------------------------------------------------------
# Cross-stack dependency analysis
# ---------------------------------------------------------------------------

def build_export_index(parsed_templates):
    """
    Build a lookup table: rendered_export_name -> (path, output_logical_id).
    Used to match Fn::ImportValue calls back to the template that owns them.
    """
    index = {}
    for path, data in parsed_templates:
        outputs = data.get("Outputs", {}) or {}
        for output_key, spec in outputs.items():
            if not isinstance(spec, dict):
                continue
            export = spec.get("Export", {})
            if not isinstance(export, dict):
                continue
            name_val = export.get("Name")
            if name_val is None:
                continue
            rendered = render_value(name_val)
            index[rendered] = (path, output_key)
    return index


def build_dependency_map(parsed_templates):
    """
    Match each template's Fn::ImportValue calls to the template that exports them.

    Returns:
        deps: {importer_path: {exporter_path: [(output_key, rendered_name), ...]}}
        unresolved: {importer_path: [rendered_name, ...]}
            (imports with no matching export in this run — likely external stacks)
    """
    export_index = build_export_index(parsed_templates)
    deps = {}
    unresolved = {}

    for path, data in parsed_templates:
        resources = data.get("Resources", {}) or {}
        raw_imports = collect_raw_imports(resources)

        for raw_val in raw_imports:
            rendered = render_value(raw_val)
            if rendered in export_index:
                exporter_path, output_key = export_index[rendered]
                if exporter_path == path:
                    continue  # self-reference, ignore
                deps.setdefault(path, {}).setdefault(exporter_path, [])
                entry = (output_key, rendered)
                if entry not in deps[path][exporter_path]:
                    deps[path][exporter_path].append(entry)
            else:
                unresolved.setdefault(path, [])
                if rendered not in unresolved[path]:
                    unresolved[path].append(rendered)

    return deps, unresolved


def topological_order(parsed_templates, deps):
    """
    Return templates sorted in dependency order (exporters before importers).
    Uses Kahn's algorithm on the dependency graph. Falls back to original order
    on cycles (shouldn't happen in valid CFN, but defensive).
    """
    paths = [p for p, _ in parsed_templates]
    path_set = set(paths)

    # Build adjacency: exporter -> set of importers
    # and in-degree: number of templates this one depends on
    in_degree = {p: 0 for p in paths}
    dependents = {p: set() for p in paths}  # who depends on p

    for importer, exporters in deps.items():
        if importer not in path_set:
            continue
        for exporter in exporters:
            if exporter not in path_set:
                continue
            in_degree[importer] += 1
            dependents[exporter].add(importer)

    from collections import deque
    queue = deque(p for p in paths if in_degree[p] == 0)
    order = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for dependent in sorted(dependents[node], key=str):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # If cycle detected, append any remaining nodes in original order
    remaining = [p for p in paths if p not in set(order)]
    order.extend(remaining)

    return order


def format_dependency_map(parsed_templates, deps, unresolved):
    """Return lines for the dependency map section of the global summary."""
    lines = []

    if not deps and not unresolved:
        lines.append("  No cross-stack dependencies detected in this run.")
        return lines

    if deps:
        for importer in sorted(deps.keys(), key=str):
            for exporter in sorted(deps[importer].keys(), key=str):
                entries = deps[importer][exporter]
                lines.append(
                    f"  {importer.name}  <-- depends on --  {exporter.name}"
                )
                for output_key, rendered_name in sorted(entries):
                    lines.append(f"      {output_key}  ({rendered_name})")
        lines.append("")

    if unresolved:
        lines.append("  UNRESOLVED IMPORTS (exported by stacks outside this run):")
        for importer in sorted(unresolved.keys(), key=str):
            lines.append(f"    {importer.name}:")
            for name in sorted(unresolved[importer]):
                lines.append(f"      - {name}")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# CFN file detection
# ---------------------------------------------------------------------------

def is_cfn_template(data):
    """Return True if the parsed data looks like a CFN template."""
    if not isinstance(data, dict):
        return False
    return "Resources" in data or "AWSTemplateFormatVersion" in data


def load_template(path: Path):
    """
    Load and return a parsed CFN template dict, or None if unreadable / not CFN.
    """
    suffix = path.suffix.lower()

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, f"Could not read file: {e}"

    if suffix in (".yaml", ".yml"):
        if not YAML_AVAILABLE:
            return None, "PyYAML not installed — cannot parse YAML files. Run: pip install pyyaml"
        try:
            data = yaml.load(text, Loader=CfnLoader)
        except yaml.YAMLError as e:
            return None, f"YAML parse error: {e}"
    elif suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return None, f"JSON parse error: {e}"
    else:
        return None, f"Unsupported extension '{suffix}'"

    if not is_cfn_template(data):
        return None, "Not a CloudFormation template (no 'Resources' or 'AWSTemplateFormatVersion' key)"

    return data, None


# ---------------------------------------------------------------------------
# Template summarizer
# ---------------------------------------------------------------------------

def summarize_parameters(params):
    lines = []
    for name, spec in sorted((params or {}).items()):
        if not isinstance(spec, dict):
            continue
        ptype = spec.get("Type", "String")
        desc = spec.get("Description", "")
        default = spec.get("Default")
        allowed = spec.get("AllowedValues")

        line = f"  - {name} ({ptype})"
        if desc:
            line += f": {_truncate(desc, 80)}"
        if default is not None:
            line += f"  [default: {render_value(default)}]"
        if allowed:
            line += f"  [allowed: {render_value(allowed)}]"
        lines.append(line)
    return lines


def summarize_resource(logical_id, spec):
    """Return a list of lines summarising a single CFN resource."""
    if not isinstance(spec, dict):
        return [f"  - {logical_id}: (malformed resource entry)"]

    rtype = spec.get("Type", "Unknown")
    props = spec.get("Properties", {}) or {}
    depends = spec.get("DependsOn")
    condition = spec.get("Condition")
    deletion_policy = spec.get("DeletionPolicy")

    lines = [f"  - {logical_id} ({rtype})"]

    # Key properties for this type
    key_props = KEY_PROPERTIES.get(rtype, [])
    shown = set()

    for key in key_props:
        if key in props:
            val = props[key]
            if key in TRUNCATE_KEYS:
                lines.append(f"      {key}: [truncated — inline policy/document]")
            else:
                lines.append(f"      {key}: {render_value(val)}")
            shown.add(key)

    # Surface any remaining properties that contain cross-stack refs
    for key, val in props.items():
        if key in shown or key in TRUNCATE_KEYS:
            continue
        imports = collect_import_values(val)
        if imports:
            lines.append(f"      {key}: {render_value(val)}  [imports: {', '.join(imports)}]")

    if condition:
        lines.append(f"      Condition: {condition}")
    if deletion_policy:
        lines.append(f"      DeletionPolicy: {deletion_policy}")
    if depends:
        if isinstance(depends, list):
            lines.append(f"      DependsOn: {', '.join(depends)}")
        else:
            lines.append(f"      DependsOn: {depends}")

    return lines


def summarize_outputs(outputs):
    lines = []
    for name, spec in sorted((outputs or {}).items()):
        if not isinstance(spec, dict):
            continue
        desc = spec.get("Description", "")
        value = spec.get("Value")
        export = spec.get("Export", {})
        export_name = export.get("Name") if isinstance(export, dict) else None

        line = f"  - {name}"
        if desc:
            line += f": {_truncate(desc, 80)}"
        if value is not None:
            line += f"  -> {render_value(value)}"
        if export_name is not None:
            line += f"  [exported as: {render_value(export_name)}]"
        lines.append(line)
    return lines


def summarize_conditions(conditions):
    lines = []
    for name in sorted((conditions or {}).keys()):
        lines.append(f"  - {name}")
    return lines


def format_template(path: Path, data: dict):
    """
    Return a list of lines representing the full formatted summary of one template.
    """
    lines = []
    lines.append(f"{'='*80}")
    lines.append(f"FILE: {path}")
    lines.append(f"{'='*80}")
    lines.append("")

    # Description
    desc = data.get("Description")
    if desc:
        lines.append(f"DESCRIPTION:")
        lines.append(f"  {_truncate(desc, 200)}")
        lines.append("")

    # AWSTemplateFormatVersion
    version = data.get("AWSTemplateFormatVersion")
    if version:
        lines.append(f"TEMPLATE VERSION: {version}")
        lines.append("")

    # Transform (SAM, etc.)
    transform = data.get("Transform")
    if transform:
        lines.append(f"TRANSFORM: {render_value(transform)}")
        lines.append("")

    # Parameters
    params = data.get("Parameters", {})
    if params:
        lines.append(f"PARAMETERS: ({len(params)})")
        lines.extend(summarize_parameters(params))
        lines.append("")

    # Conditions
    conditions = data.get("Conditions", {})
    if conditions:
        lines.append(f"CONDITIONS: ({len(conditions)})")
        lines.extend(summarize_conditions(conditions))
        lines.append("")

    # Resources
    resources = data.get("Resources", {})
    if resources:
        lines.append(f"RESOURCES: ({len(resources)})")
        for logical_id, spec in sorted(resources.items()):
            lines.extend(summarize_resource(logical_id, spec))
        lines.append("")

    # Wiring (intra-template resource references)
    if resources:
        resource_ids = set(resources.keys())
        wiring = []
        for lid, spec in resources.items():
            if not isinstance(spec, dict):
                continue
            for target, via, kind in extract_intra_refs(lid, spec, resource_ids):
                wiring.append((lid, target, via, kind))
        if wiring:
            lines.append(f"WIRING: ({len(wiring)} connection(s))")
            for src, tgt, via, kind in sorted(wiring):
                lines.append(f"  {src}  ->  {tgt}  [{via}]")
            lines.append("")

    # Outputs
    outputs = data.get("Outputs", {})
    if outputs:
        lines.append(f"OUTPUTS: ({len(outputs)})")
        lines.extend(summarize_outputs(outputs))
        lines.append("")

    # Cross-stack: imports and exports
    all_imports = collect_import_values(resources)
    exports = collect_exports(outputs)

    if all_imports:
        lines.append("CROSS-STACK IMPORTS (Fn::ImportValue):")
        for imp in sorted(set(all_imports)):
            lines.append(f"  - {imp}")
        lines.append("")

    if exports:
        lines.append("CROSS-STACK EXPORTS:")
        for exp in exports:
            lines.append(f"  - {exp}")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

CFN_EXTENSIONS = {".yaml", ".yml", ".json"}


def find_cfn_files(targets):
    """
    Given a list of paths (files or directories), return a sorted list of
    candidate CFN file Paths.
    """
    found = []
    for target in targets:
        p = Path(target)
        if not p.exists():
            print(f"[warn] Path does not exist: {target}", file=sys.stderr)
            continue
        if p.is_file():
            if p.suffix.lower() in CFN_EXTENSIONS:
                found.append(p)
            else:
                print(f"[warn] Skipping non-CFN file: {target}", file=sys.stderr)
        elif p.is_dir():
            for ext in CFN_EXTENSIONS:
                found.extend(p.rglob(f"*{ext}"))
    # Deduplicate while preserving order
    seen = set()
    result = []
    for f in found:
        resolved = f.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(f)
    return sorted(result)


# ---------------------------------------------------------------------------
# Global summary
# ---------------------------------------------------------------------------

def global_summary(parsed_templates):
    """
    Return lines for the global infrastructure summary across all templates.
    parsed_templates: list of (path, data) tuples.
    """
    lines = []
    lines.append(f"{'='*80}")
    lines.append("INFRASTRUCTURE SUMMARY")
    lines.append(f"{'='*80}")
    lines.append("")

    total_resources = 0
    type_counts = {}
    all_imports = []
    all_exports = []
    stack_refs = []

    for path, data in parsed_templates:
        resources = data.get("Resources", {}) or {}
        outputs = data.get("Outputs", {}) or {}
        total_resources += len(resources)

        for spec in resources.values():
            if isinstance(spec, dict):
                rtype = spec.get("Type", "Unknown")
                type_counts[rtype] = type_counts.get(rtype, 0) + 1

                # Nested stack refs
                if rtype == "AWS::CloudFormation::Stack":
                    props = spec.get("Properties", {}) or {}
                    url = props.get("TemplateURL")
                    if url:
                        stack_refs.append(f"{path.name}: {render_value(url)}")

        all_imports.extend(collect_import_values(resources))
        all_exports.extend(collect_exports(outputs))

    lines.append(f"Total templates parsed : {len(parsed_templates)}")
    lines.append(f"Total resources        : {total_resources}")
    lines.append("")

    if type_counts:
        lines.append("RESOURCE TYPES:")
        for rtype, count in sorted(type_counts.items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"  {count:>4}x  {rtype}")
        lines.append("")

    if all_imports:
        lines.append("ALL CROSS-STACK IMPORTS:")
        for imp in sorted(set(all_imports)):
            lines.append(f"  - {imp}")
        lines.append("")

    if all_exports:
        lines.append("ALL CROSS-STACK EXPORTS:")
        for exp in sorted(set(all_exports)):
            lines.append(f"  - {exp}")
        lines.append("")

    if stack_refs:
        lines.append("NESTED STACK REFERENCES:")
        for ref in stack_refs:
            lines.append(f"  - {ref}")
        lines.append("")

    # Cross-stack dependency map
    deps, unresolved = build_dependency_map(parsed_templates)
    lines.append("CROSS-STACK DEPENDENCY MAP:")
    lines.extend(format_dependency_map(parsed_templates, deps, unresolved))

    # Deployment order (only meaningful if there are dependencies)
    if deps:
        order = topological_order(parsed_templates, deps)
        lines.append("SUGGESTED DEPLOYMENT ORDER:")
        for i, path in enumerate(order, 1):
            lines.append(f"  {i}. {path.name}")
        lines.append("")

    # Pattern detection
    patterns = detect_patterns(type_counts)
    if patterns:
        lines.append("DETECTED INFRASTRUCTURE PATTERNS:")
        for p in patterns:
            lines.append(f"  - {p}")
        lines.append("")

    return lines


def detect_patterns(type_counts):
    """Heuristically identify common infrastructure patterns from resource types."""
    patterns = []
    types = set(type_counts.keys())

    if "AWS::EC2::VPC" in types:
        has_nat = "AWS::EC2::NatGateway" in types
        has_igw = "AWS::EC2::InternetGateway" in types
        subnets = type_counts.get("AWS::EC2::Subnet", 0)
        desc = f"VPC networking ({subnets} subnet(s)"
        if has_igw:
            desc += ", internet gateway"
        if has_nat:
            desc += ", NAT gateway"
        desc += ")"
        patterns.append(desc)

    if "AWS::ECS::Service" in types or "AWS::ECS::TaskDefinition" in types:
        patterns.append("ECS container workloads")

    if "AWS::EKS::Cluster" in types:
        patterns.append("EKS Kubernetes cluster")

    if "AWS::Lambda::Function" in types:
        count = type_counts["AWS::Lambda::Function"]
        patterns.append(f"Serverless / Lambda ({count} function(s))")

    if "AWS::ApiGateway::RestApi" in types or "AWS::ApiGatewayV2::Api" in types:
        patterns.append("API Gateway")

    if "AWS::RDS::DBInstance" in types or "AWS::RDS::DBCluster" in types:
        patterns.append("RDS relational database")

    if "AWS::DynamoDB::Table" in types:
        patterns.append("DynamoDB NoSQL tables")

    if "AWS::ElastiCache::ReplicationGroup" in types:
        patterns.append("ElastiCache (Redis/Memcached)")

    if "AWS::MSK::Cluster" in types or "AWS::Kinesis::Stream" in types:
        patterns.append("Streaming / messaging infrastructure")

    if "AWS::ElasticLoadBalancingV2::LoadBalancer" in types:
        lbtype = "load balancing (ALB/NLB)"
        patterns.append(lbtype)

    if "AWS::CloudFront::Distribution" in types:
        patterns.append("CloudFront CDN")

    if "AWS::S3::Bucket" in types:
        count = type_counts["AWS::S3::Bucket"]
        patterns.append(f"S3 storage ({count} bucket(s))")

    if "AWS::StepFunctions::StateMachine" in types:
        patterns.append("Step Functions state machines")

    if "AWS::Glue::Job" in types or "AWS::Glue::Crawler" in types:
        patterns.append("AWS Glue data pipeline")

    if "AWS::CodePipeline::Pipeline" in types:
        patterns.append("CI/CD pipeline (CodePipeline)")

    if "AWS::IAM::Role" in types:
        count = type_counts["AWS::IAM::Role"]
        patterns.append(f"IAM roles ({count} role(s))")

    if "AWS::WAFv2::WebACL" in types:
        patterns.append("WAFv2 web ACL (application firewall)")

    if "AWS::Cognito::UserPool" in types:
        patterns.append("Cognito user authentication")

    if "AWS::CloudFormation::Stack" in types:
        count = type_counts["AWS::CloudFormation::Stack"]
        patterns.append(f"Nested CloudFormation stacks ({count})")

    return patterns


# ---------------------------------------------------------------------------
# Intra-template wiring extraction
# ---------------------------------------------------------------------------

# Map AWS resource types to a visual category used in the HTML diagram
_CATEGORY_MAP = {
    "AWS::EC2::VPC":                             "networking",
    "AWS::EC2::Subnet":                          "networking",
    "AWS::EC2::RouteTable":                      "networking",
    "AWS::EC2::Route":                           "networking",
    "AWS::EC2::InternetGateway":                 "networking",
    "AWS::EC2::VPCGatewayAttachment":            "networking",
    "AWS::EC2::SubnetRouteTableAssociation":      "networking",
    "AWS::EC2::NatGateway":                      "networking",
    "AWS::EC2::EIP":                             "networking",
    "AWS::EC2::TransitGateway":                  "networking",
    "AWS::EC2::TransitGatewayAttachment":         "networking",
    "AWS::EC2::Instance":                        "compute",
    "AWS::EC2::LaunchTemplate":                  "compute",
    "AWS::EC2::SecurityGroup":                   "security",
    "AWS::EC2::SecurityGroupIngress":            "security",
    "AWS::EC2::SecurityGroupEgress":             "security",
    "AWS::AutoScaling::AutoScalingGroup":        "compute",
    "AWS::AutoScaling::ScalingPolicy":           "compute",
    "AWS::Lambda::Function":                     "lambda",
    "AWS::Lambda::Permission":                   "lambda",
    "AWS::Lambda::Alias":                        "lambda",
    "AWS::Lambda::Version":                      "lambda",
    "AWS::Lambda::EventSourceMapping":           "lambda",
    "AWS::Lambda::LayerVersion":                 "lambda",
    "AWS::ApiGateway::RestApi":                  "api",
    "AWS::ApiGateway::Stage":                    "api",
    "AWS::ApiGateway::Resource":                 "api",
    "AWS::ApiGateway::Method":                   "api",
    "AWS::ApiGatewayV2::Api":                    "api",
    "AWS::ApiGatewayV2::Stage":                  "api",
    "AWS::ApiGatewayV2::Integration":            "api",
    "AWS::ApiGatewayV2::Route":                  "api",
    "AWS::ElasticLoadBalancingV2::LoadBalancer": "loadbalancer",
    "AWS::ElasticLoadBalancingV2::Listener":     "loadbalancer",
    "AWS::ElasticLoadBalancingV2::TargetGroup":  "loadbalancer",
    "AWS::ElasticLoadBalancingV2::ListenerRule": "loadbalancer",
    "AWS::RDS::DBInstance":                      "database",
    "AWS::RDS::DBCluster":                       "database",
    "AWS::RDS::DBSubnetGroup":                   "database",
    "AWS::RDS::DBClusterParameterGroup":         "database",
    "AWS::RDS::DBParameterGroup":                "database",
    "AWS::DynamoDB::Table":                      "database",
    "AWS::ElastiCache::ReplicationGroup":        "database",
    "AWS::ElastiCache::SubnetGroup":             "database",
    "AWS::S3::Bucket":                           "storage",
    "AWS::S3::BucketPolicy":                     "storage",
    "AWS::SQS::Queue":                           "messaging",
    "AWS::SNS::Topic":                           "messaging",
    "AWS::SNS::Subscription":                    "messaging",
    "AWS::Kinesis::Stream":                      "messaging",
    "AWS::MSK::Cluster":                         "messaging",
    "AWS::Events::Rule":                         "messaging",
    "AWS::IAM::Role":                            "iam",
    "AWS::IAM::InstanceProfile":                 "iam",
    "AWS::IAM::Policy":                          "iam",
    "AWS::IAM::ManagedPolicy":                   "iam",
    "AWS::KMS::Key":                             "security",
    "AWS::KMS::Alias":                           "security",
    "AWS::SecretsManager::Secret":               "security",
    "AWS::Cognito::UserPool":                    "security",
    "AWS::Cognito::UserPoolClient":              "security",
    "AWS::CloudWatch::Alarm":                    "monitoring",
    "AWS::CloudWatch::Dashboard":                "monitoring",
    "AWS::Logs::LogGroup":                       "monitoring",
    "AWS::CodePipeline::Pipeline":               "cicd",
    "AWS::CodeBuild::Project":                   "cicd",
    "AWS::CloudFront::Distribution":             "edge",
    "AWS::Route53::RecordSet":                   "edge",
    "AWS::Route53::HostedZone":                  "edge",
    "AWS::ACM::Certificate":                     "edge",
    "AWS::WAFv2::WebACL":                        "edge",
    "AWS::ECS::Cluster":                         "container",
    "AWS::ECS::Service":                         "container",
    "AWS::ECS::TaskDefinition":                  "container",
    "AWS::EKS::Cluster":                         "container",
    "AWS::EKS::Nodegroup":                       "container",
    "AWS::StepFunctions::StateMachine":          "analytics",
    "AWS::Glue::Job":                            "analytics",
    "AWS::Glue::Database":                       "analytics",
    "AWS::Glue::Crawler":                        "analytics",
}


def _resource_category(rtype):
    """Return a broad visual category for an AWS resource type."""
    return _CATEGORY_MAP.get(rtype, "other")


def extract_intra_refs(logical_id, spec, resource_ids):
    """
    Walk a resource's property tree and collect all references to other
    resources within the same template.

    Returns a list of (target_id, via_label, kind) tuples:
      target_id  — logical ID of the referenced resource
      via_label  — top-level property key where the reference appears
      kind       — 'Ref' | 'GetAtt.<attr>' | 'DependsOn'
    """
    edges = []
    props = spec.get("Properties", {}) or {}

    def walk(obj, top_key):
        if isinstance(obj, dict):
            if "Ref" in obj and len(obj) == 1:
                target = obj["Ref"]
                if target in resource_ids and target != logical_id:
                    edges.append((target, top_key, "Ref"))
                return
            if "Fn::GetAtt" in obj and len(obj) == 1:
                parts = obj["Fn::GetAtt"]
                if isinstance(parts, list) and len(parts) >= 1:
                    target, attr = parts[0], (parts[1] if len(parts) > 1 else "")
                elif isinstance(parts, str) and "." in parts:
                    target, attr = parts.split(".", 1)
                else:
                    return
                if target in resource_ids and target != logical_id:
                    edges.append((target, top_key, f"GetAtt.{attr}"))
                return
            for k, v in obj.items():
                walk(v, top_key or k)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, top_key)

    for key, val in props.items():
        walk(val, key)

    # Explicit DependsOn
    depends = spec.get("DependsOn")
    if depends:
        if isinstance(depends, str):
            depends = [depends]
        for dep in (depends if isinstance(depends, list) else []):
            if dep in resource_ids and dep != logical_id:
                edges.append((dep, "DependsOn", "DependsOn"))

    # Deduplicate
    seen = set()
    result = []
    for e in edges:
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result


# ---------------------------------------------------------------------------
# Graph data builder (used by HTML diagram)
# ---------------------------------------------------------------------------

def build_graph_data(parsed_templates):
    """Build nodes + directed edges for all parsed templates."""
    templates_out = []

    for path, data in parsed_templates:
        resources = data.get("Resources", {}) or {}
        resource_ids = set(resources.keys())
        nodes, edges = [], []

        for lid, spec in sorted(resources.items()):
            if not isinstance(spec, dict):
                continue
            rtype = spec.get("Type", "Unknown")
            res_props = spec.get("Properties", {}) or {}

            # Render key properties for inline display in the diagram
            rendered_props = []
            for k in KEY_PROPERTIES.get(rtype, [])[:7]:
                if k in res_props and k not in TRUNCATE_KEYS:
                    val = render_value(res_props[k])
                    if len(val) > 20:
                        val = val[:19] + "\u2026"
                    rendered_props.append({"key": k, "value": val})

            nodes.append({
                "id":             f"{path.stem}::{lid}",
                "label":          lid,
                "type":           rtype,
                "category":       _resource_category(rtype),
                "condition":      spec.get("Condition"),
                "deletion_policy": spec.get("DeletionPolicy"),
                "template":       path.stem,
                "props":          rendered_props,
            })
            for target, via, kind in extract_intra_refs(lid, spec, resource_ids):
                edges.append({
                    "source": f"{path.stem}::{lid}",
                    "target": f"{path.stem}::{target}",
                    "via":    via,
                    "kind":   kind,
                })

        # Deduplicate parallel edges — keep first via label per source→target pair
        seen_pairs = set()
        deduped = []
        for e in edges:
            k = (e["source"], e["target"])
            if k not in seen_pairs:
                seen_pairs.add(k)
                deduped.append(e)

        templates_out.append({
            "id":          path.stem,
            "name":        path.name,
            "description": data.get("Description") or "",
            "nodes":       nodes,
            "edges":       deduped,
        })

    # Cross-stack edges
    deps, _ = build_dependency_map(parsed_templates)
    cross_edges = []
    for imp_path, exporters in deps.items():
        for exp_path, entries in exporters.items():
            for output_key, _ in entries:
                cross_edges.append({
                    "from_template": imp_path.stem,
                    "to_template":   exp_path.stem,
                    "via":           output_key,
                })

    return {"templates": templates_out, "cross_edges": cross_edges}


# ---------------------------------------------------------------------------
# Docs / README generator
# ---------------------------------------------------------------------------

def generate_ai_context(parsed_templates, type_counts, patterns, deps):
    """
    Generate a dense, LLM-ready context block — suitable for pasting into
    Claude, Copilot, or any chat model as a system/context message.
    """
    lines = []
    lines.append("CLOUDFORMATION STACK CONTEXT")
    lines.append("=" * 60)
    lines.append(f"Templates : {len(parsed_templates)}")
    lines.append(f"Resources : {sum(type_counts.values())}")
    if patterns:
        lines.append(f"Patterns  : {', '.join(patterns)}")
    lines.append("")

    for path, data in parsed_templates:
        resources = data.get("Resources", {}) or {}
        params    = data.get("Parameters", {}) or {}
        outputs   = data.get("Outputs", {}) or {}
        desc      = data.get("Description", "")

        lines.append(f"--- {path.name} ---")
        if desc:
            lines.append(f"Description: {_truncate(desc, 120)}")

        # Group resources by type and list logical IDs
        type_lids = {}
        for lid, spec in sorted(resources.items()):
            if isinstance(spec, dict):
                rtype = spec.get("Type", "Unknown")
                type_lids.setdefault(rtype, []).append(lid)
        for rtype, lids in sorted(type_lids.items()):
            if len(lids) == 1:
                lines.append(f"  {rtype}: {lids[0]}")
            else:
                lines.append(f"  {rtype} ({len(lids)}x): {', '.join(lids)}")

        if params:
            lines.append(f"Parameters ({len(params)}): {', '.join(sorted(params.keys()))}")
        if outputs:
            lines.append(f"Outputs ({len(outputs)}): {', '.join(sorted(outputs.keys()))}")
        lines.append("")

    if deps:
        lines.append("Cross-stack dependencies:")
        for importer in sorted(deps.keys(), key=str):
            for exporter in sorted(deps[importer].keys(), key=str):
                entries = deps[importer][exporter]
                via = ", ".join(ok for ok, _ in sorted(entries))
                lines.append(f"  {importer.name}  -->  {exporter.name}  (via {via})")
        lines.append("")

    lines.append("Use this context to:")
    lines.append("  - Answer questions about the infrastructure")
    lines.append("  - Suggest modifications and their downstream effects")
    lines.append("  - Identify security issues or misconfigurations")
    lines.append("  - Write new resources that fit the existing patterns")

    return "\n".join(lines)


def generate_docs(parsed_templates, regen_cmd=None):
    """
    Return a Markdown README string documenting the full stack.
    Structured for humans to read and for AI models to consume as a prompt.
    """
    import datetime

    today = datetime.date.today().isoformat()

    # Gather global stats
    type_counts = {}
    for _, data in parsed_templates:
        for spec in (data.get("Resources", {}) or {}).values():
            if isinstance(spec, dict):
                t = spec.get("Type", "Unknown")
                type_counts[t] = type_counts.get(t, 0) + 1

    patterns    = detect_patterns(type_counts)
    deps, unres = build_dependency_map(parsed_templates)

    lines = []

    # Header
    lines.append("# Infrastructure Documentation")
    lines.append("")
    if regen_cmd:
        lines.append(f"<!-- regenerate: {regen_cmd} -->")
        lines.append("")
    lines.append(f"> Auto-generated by **cfcat** on {today}.  ")
    if regen_cmd:
        lines.append(f"> To keep in sync: `{regen_cmd}`")
    lines.append("")

    # Architecture overview
    if patterns:
        lines.append("## Architecture Overview")
        lines.append("")
        for p in patterns:
            lines.append(f"- {p}")
        lines.append("")

    # Resource type inventory
    if type_counts:
        lines.append("## Resource Inventory")
        lines.append("")
        lines.append("| Count | Type |")
        lines.append("|------:|------|")
        for rtype, count in sorted(type_counts.items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"| {count} | `{rtype}` |")
        lines.append("")

    # Per-template sections
    lines.append("## Templates")
    lines.append("")

    for path, data in parsed_templates:
        resources = data.get("Resources", {}) or {}
        params    = data.get("Parameters", {}) or {}
        outputs   = data.get("Outputs", {}) or {}
        desc      = data.get("Description", "")

        lines.append(f"### `{path.name}`")
        lines.append("")
        if desc:
            lines.append(f"> {_truncate(desc, 200)}")
            lines.append("")
        lines.append(
            f"**Resources:** {len(resources)}"
            f" &nbsp;|&nbsp; **Parameters:** {len(params)}"
            f" &nbsp;|&nbsp; **Outputs:** {len(outputs)}"
        )
        lines.append("")

        # Parameters table
        if params:
            lines.append("#### Parameters")
            lines.append("")
            lines.append("| Name | Type | Description | Default |")
            lines.append("|------|------|-------------|---------|")
            for name, spec in sorted(params.items()):
                if not isinstance(spec, dict):
                    continue
                ptype   = spec.get("Type", "String")
                pdesc   = _truncate(spec.get("Description", ""), 70)
                default = spec.get("Default")
                defstr  = f"`{render_value(default)}`" if default is not None else ""
                lines.append(f"| `{name}` | {ptype} | {pdesc} | {defstr} |")
            lines.append("")

        # Resources grouped by category
        if resources:
            lines.append("#### Resources")
            lines.append("")
            by_cat = {}
            for lid, spec in sorted(resources.items()):
                if not isinstance(spec, dict):
                    continue
                rtype = spec.get("Type", "Unknown")
                cat   = _resource_category(rtype)
                by_cat.setdefault(cat, []).append((lid, rtype))

            for cat in sorted(by_cat.keys()):
                lines.append(f"**{cat.title()}**")
                lines.append("")
                for lid, rtype in by_cat[cat]:
                    lines.append(f"- `{lid}` — *{rtype}*")
                lines.append("")

        # Outputs / exports
        if outputs:
            lines.append("#### Outputs")
            lines.append("")
            for name, spec in sorted(outputs.items()):
                if not isinstance(spec, dict):
                    continue
                odesc  = spec.get("Description", "")
                value  = spec.get("Value")
                export = spec.get("Export", {})
                exp_name = export.get("Name") if isinstance(export, dict) else None

                line = f"- `{name}`"
                if odesc:
                    line += f" — {_truncate(odesc, 80)}"
                if value is not None:
                    line += f"  →  `{render_value(value)}`"
                if exp_name is not None:
                    line += f"  *(exported as `{render_value(exp_name)}`)*"
                lines.append(line)
            lines.append("")

    # Cross-stack dependencies
    if deps or unres:
        lines.append("## Cross-Stack Dependencies")
        lines.append("")

        if deps:
            for importer in sorted(deps.keys(), key=str):
                for exporter in sorted(deps[importer].keys(), key=str):
                    entries = deps[importer][exporter]
                    lines.append(f"- **`{importer.name}`** depends on **`{exporter.name}`**")
                    for output_key, rendered_name in sorted(entries):
                        lines.append(f"  - via `{output_key}` (`{rendered_name}`)")
            lines.append("")

        if unres:
            lines.append("### Unresolved Imports")
            lines.append("")
            lines.append("These imports reference stacks not included in this run:")
            lines.append("")
            for importer in sorted(unres.keys(), key=str):
                for name in sorted(unres[importer]):
                    lines.append(f"- `{importer.name}` → `{name}`")
            lines.append("")

    # Deployment order
    if deps:
        order = topological_order(parsed_templates, deps)
        lines.append("## Deployment Order")
        lines.append("")
        for i, p in enumerate(order, 1):
            lines.append(f"{i}. `{p.name}`")
        lines.append("")

    # Git hook setup
    lines.append("## Keeping Docs in Sync")
    lines.append("")
    lines.append("Add cfcat as a pre-commit hook so this file regenerates automatically:")
    lines.append("")
    lines.append("```sh")
    lines.append("# .git/hooks/pre-commit  (chmod +x after creating)")
    lines.append("#!/usr/bin/env sh")
    if regen_cmd:
        lines.append(regen_cmd)
    else:
        lines.append("cfcat *.yaml --docs")
    lines.append("git add INFRA.md   # or whatever --output basename + .md")
    lines.append("```")
    lines.append("")

    # AI prompt context block
    lines.append("---")
    lines.append("")
    lines.append("## AI Prompt Context")
    lines.append("")
    lines.append(
        "> Drop the block below into Claude or Copilot as a context/system message"
        " for stack-aware assistance."
    )
    lines.append("")
    lines.append("```")
    lines.append(generate_ai_context(parsed_templates, type_counts, patterns, deps))
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML diagram generator
# ---------------------------------------------------------------------------

def generate_html_diagram(parsed_templates):
    """Return a self-contained interactive HTML wiring diagram string."""
    graph = build_graph_data(parsed_templates)
    graph_json = json.dumps(graph, separators=(",", ":"))

    lines = []

    def L(s=""):
        lines.append(s)

    L("<!DOCTYPE html>")
    L('<html lang="en">')
    L("<head>")
    L('<meta charset="UTF-8">')
    L("<title>cfcat \u2014 Wiring Diagram</title>")
    L("<style>")
    L("* { box-sizing: border-box; margin: 0; padding: 0; }")
    L("body { background: #0d1117; color: #c9d1d9; font-family: 'SF Mono','Fira Code',monospace; font-size: 12px; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }")
    L("header { padding: 14px 20px 0; flex-shrink: 0; }")
    L("h1 { font-size: 14px; color: #ff7b72; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 2px; }")
    L(".sub { font-size: 10px; color: #8b949e; letter-spacing: .5px; }")
    L(".tabs { display: flex; gap: 4px; padding: 10px 20px 0; flex-shrink: 0; border-bottom: 1px solid #21262d; overflow-x: auto; }")
    L(".tab { background: transparent; border: 1px solid transparent; border-bottom: none; color: #8b949e; font-family: inherit; font-size: 11px; padding: 6px 14px; cursor: pointer; border-radius: 6px 6px 0 0; white-space: nowrap; }")
    L(".tab:hover { color: #c9d1d9; background: #161b22; }")
    L(".tab.active { color: #ffa657; background: #161b22; border-color: #30363d; border-bottom-color: #161b22; }")
    L(".tmpl-meta { padding: 5px 20px; font-size: 10px; color: #8b949e; flex-shrink: 0; background: #161b22; border-bottom: 1px solid #21262d; }")
    L(".tmpl-meta strong { color: #c9d1d9; }")
    L(".main { display: flex; flex: 1; overflow: hidden; }")
    L("#diagram-wrap { flex: 1; overflow: hidden; }")
    L("svg { width: 100%; height: 100%; }")
    L("#detail-panel { width: 240px; background: #161b22; border-left: 1px solid #30363d; padding: 16px; overflow-y: auto; display: none; flex-shrink: 0; }")
    L(".legend { display: flex; flex-wrap: wrap; gap: 10px; padding: 7px 20px; background: #0d1117; border-top: 1px solid #21262d; flex-shrink: 0; }")
    L(".legend-item { display: flex; align-items: center; gap: 5px; font-size: 9.5px; color: #8b949e; }")
    L(".legend-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }")
    L("</style>")
    L("</head>")
    L("<body>")
    L("<header><h1>Wiring Diagram</h1><p class=\"sub\">cfcat \u2014 resource relationship map</p></header>")
    L("<div class=\"tabs\" id=\"tabs\"></div>")
    L("<div class=\"tmpl-meta\"><strong id=\"tmpl-desc\"></strong>&nbsp;<span id=\"tmpl-stats\"></span></div>")
    L("<div class=\"main\">")
    L("  <div id=\"diagram-wrap\">")
    L("    <svg id=\"diagram-svg\" viewBox=\"0 0 1400 900\" preserveAspectRatio=\"xMidYMid meet\">")
    L("      <defs>")
    L("        <marker id=\"arrow\" markerWidth=\"8\" markerHeight=\"6\" refX=\"7\" refY=\"3\" orient=\"auto\"><polygon points=\"0 0,8 3,0 6\" fill=\"#484f58\"/></marker>")
    L("        <marker id=\"arrow-hi\" markerWidth=\"8\" markerHeight=\"6\" refX=\"7\" refY=\"3\" orient=\"auto\"><polygon points=\"0 0,8 3,0 6\" fill=\"#f0c674\"/></marker>")
    L("        <marker id=\"arrow-dep\" markerWidth=\"8\" markerHeight=\"6\" refX=\"7\" refY=\"3\" orient=\"auto\"><polygon points=\"0 0,8 3,0 6\" fill=\"#8b949e\"/></marker>")
    L("      </defs>")
    L("      <g id=\"edge-group\"></g>")
    L("      <g id=\"node-group\"></g>")
    L("    </svg>")
    L("  </div>")
    L("  <div id=\"detail-panel\"></div>")
    L("</div>")
    L("<div class=\"legend\" id=\"legend\"></div>")
    L("<script>")
    L(f"const GRAPH = {graph_json};")
    L("""
const CATEGORY_COLOR = {
  networking:   '#79c0ff', compute:     '#ffa657', lambda:      '#56d364',
  api:          '#a5d6ff', loadbalancer:'#58a6ff', database:    '#d2a8ff',
  storage:      '#f0c674', messaging:   '#ff7b72', security:    '#b08080',
  iam:          '#e3b341', monitoring:  '#39d353', cicd:        '#bc8cff',
  edge:         '#ff9e64', container:   '#ffc947', analytics:   '#f78166',
  other:        '#6e7681',
};

const NODE_W = 210, NODE_H = 48;
const PROP_ROW_H = 15, PROP_PAD_TOP = 6, PROP_PAD_BOT = 6;
let simNodes = [], simEdges = [], nodeById = {};
let rafId = null, hoverRafId = null, hoveredNode = null, selectedNode = null;
let animStart = 0;
const ANIM_MS = 850;
const expandedNodes = new Set();

function scheduleHoverRender() {
  if (!hoverRafId) hoverRafId = requestAnimationFrame(() => { hoverRafId = null; render(); });
}
// Cancel pending hover re-renders during a click so the DOM isn't
// rebuilt between mousedown and mouseup (which swallows the click event).
document.addEventListener('mousedown', () => {
  if (hoverRafId) { cancelAnimationFrame(hoverRafId); hoverRafId = null; }
});
document.addEventListener('mouseup', () => { scheduleHoverRender(); });

function nodeHeight(nd) {
  if (!expandedNodes.has(nd.id) || !nd.props || nd.props.length === 0) return NODE_H;
  return NODE_H + PROP_PAD_TOP + nd.props.length * PROP_ROW_H + PROP_PAD_BOT;
}

// ── Hierarchical DAG layout ──────────────────────────────────────────────
// Level 0 = no outgoing edges (pure prerequisites, rendered at top).
// Level k = deepest dependency level + 1 (most dependent nodes at bottom).
// Edges therefore flow top-to-bottom, matching CFN creation order.
function computeLayout(nodes, edges) {
  const W = 1400, H = 900, PAD_X = 90, PAD_TOP = 80, PAD_BOT = 80;
  if (nodes.length === 0) return;

  const outgoing = {};
  nodes.forEach(n => outgoing[n.id] = new Set());
  edges.forEach(e => { if (e.source && e.target) outgoing[e.source.id].add(e.target.id); });

  const lvl = {}, computing = new Set();
  function getLevel(id) {
    if (lvl[id] !== undefined) return lvl[id];
    if (computing.has(id)) return (lvl[id] = 0);
    computing.add(id);
    const deps = [...outgoing[id]];
    lvl[id] = deps.length === 0 ? 0 : 1 + Math.max(...deps.map(getLevel));
    computing.delete(id);
    return lvl[id];
  }
  nodes.forEach(n => getLevel(n.id));

  const byLevel = {};
  nodes.forEach(n => { (byLevel[lvl[n.id]] = byLevel[lvl[n.id]] || []).push(n); });
  const levelNums = Object.keys(byLevel).map(Number).sort((a, b) => a - b);
  const numLevels = levelNums.length;
  const levelStep = numLevels > 1 ? (H - PAD_TOP - PAD_BOT) / (numLevels - 1) : (H - PAD_TOP - PAD_BOT) / 2;

  // Barycenter ordering: sort each row by average x of already-placed dependencies
  const placedX = {};
  levelNums.forEach((l, li) => {
    const row = byLevel[l];
    if (li > 0) {
      row.sort((a, b) => {
        const cx = n => {
          const deps = [...outgoing[n.id]].filter(id => placedX[id] !== undefined);
          return deps.length ? deps.reduce((s, id) => s + placedX[id], 0) / deps.length : W / 2;
        };
        return cx(a) - cx(b);
      });
    } else {
      row.sort((a, b) => a.label.localeCompare(b.label));
    }
    const gap = Math.max(16, Math.min(36, (W - 2 * PAD_X - row.length * NODE_W) / Math.max(1, row.length - 1)));
    const totalW = row.length * NODE_W + Math.max(0, row.length - 1) * gap;
    const startX = (W - totalW) / 2 + NODE_W / 2;
    row.forEach((n, i) => {
      n.finalX = startX + i * (NODE_W + gap);
      n.finalY = PAD_TOP + li * levelStep;
      placedX[n.id] = n.finalX;
    });
  });
}

// ── Template loading ─────────────────────────────────────────────────────
function loadTemplate(tmpl) {
  document.getElementById('tmpl-desc').textContent = tmpl.description || tmpl.name;
  document.getElementById('tmpl-stats').textContent =
    tmpl.nodes.length + ' resources \u00b7 ' + tmpl.edges.length + ' connections';
  document.getElementById('detail-panel').style.display = 'none';
  hoveredNode = null; selectedNode = null;
  expandedNodes.clear();

  const W = 1400, H = 900;
  simNodes = tmpl.nodes.map(n => ({ ...n, x: W / 2, y: H / 2, finalX: W / 2, finalY: H / 2 }));
  nodeById = {};
  simNodes.forEach(n => nodeById[n.id] = n);
  simEdges = tmpl.edges
    .map(e => ({ ...e, source: nodeById[e.source], target: nodeById[e.target] }))
    .filter(e => e.source && e.target);

  computeLayout(simNodes, simEdges);

  // Scatter start positions around each node's final position for fly-in feel
  simNodes.forEach(n => {
    const angle = Math.random() * 2 * Math.PI;
    const dist = 280 + Math.random() * 260;
    n.startX = Math.max(NODE_W / 2, Math.min(W - NODE_W / 2, n.finalX + Math.cos(angle) * dist));
    n.startY = Math.max(NODE_H / 2, Math.min(H - NODE_H / 2, n.finalY + Math.sin(angle) * dist));
    n.x = n.startX;
    n.y = n.startY;
  });

  animStart = 0;
  if (rafId) cancelAnimationFrame(rafId);
  rafId = requestAnimationFrame(tick);
}

// ── Fly-in animation (lerp to layout positions) ──────────────────────────
function tick(ts) {
  if (!animStart) animStart = ts;
  const t = Math.min(1, (ts - animStart) / ANIM_MS);
  const ease = 1 - Math.pow(1 - t, 3);
  simNodes.forEach(n => {
    n.x = n.startX + (n.finalX - n.startX) * ease;
    n.y = n.startY + (n.finalY - n.startY) * ease;
  });
  render();
  if (t < 1) rafId = requestAnimationFrame(tick);
  else rafId = null;
}

// ── Helpers ──────────────────────────────────────────────────────────────
function edgePt(node, tx, ty) {
  const nh = nodeHeight(node);
  const dx = tx - node.x, dy = ty - node.y;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  const ux = dx / len, uy = dy / len;
  const tX = ux !== 0 ? Math.abs(NODE_W / 2 / ux) : 1e9;
  const tY = uy !== 0 ? Math.abs(nh / 2 / uy) : 1e9;
  return { x: node.x + ux * Math.min(tX, tY), y: node.y + uy * Math.min(tX, tY) };
}

function svgEl(tag) { return document.createElementNS('http://www.w3.org/2000/svg', tag); }
function attrs(el, obj) { Object.entries(obj).forEach(([k, v]) => el.setAttribute(k, v)); return el; }

// ── Render ───────────────────────────────────────────────────────────────
function render() {
  const eg = document.getElementById('edge-group');
  const ng = document.getElementById('node-group');
  while (eg.lastChild) eg.removeChild(eg.lastChild);
  while (ng.lastChild) ng.removeChild(ng.lastChild);

  const connIds = new Set();
  if (hoveredNode) {
    simEdges.forEach(e => {
      if (e.source === hoveredNode || e.target === hoveredNode) {
        connIds.add(e.source.id); connIds.add(e.target.id);
      }
    });
  }

  // Edges: S-curve (cubic bezier, vertical control points) for clean top-to-bottom flow
  simEdges.forEach(e => {
    const hi  = hoveredNode && (e.source === hoveredNode || e.target === hoveredNode);
    const dim = hoveredNode && !hi;
    const sp = edgePt(e.source, e.target.x, e.target.y);
    const ep = edgePt(e.target, e.source.x, e.source.y);
    const my = (sp.y + ep.y) / 2;
    const d = `M ${sp.x} ${sp.y} C ${sp.x} ${my} ${ep.x} ${my} ${ep.x} ${ep.y}`;

    const p = attrs(svgEl('path'), {
      d,
      stroke: hi ? '#f0c674' : e.kind === 'DependsOn' ? '#555' : '#30363d',
      'stroke-width': hi ? 2 : 1.2,
      fill: 'none',
      opacity: dim ? 0.07 : 0.9,
      'marker-end': hi ? 'url(#arrow-hi)' : e.kind === 'DependsOn' ? 'url(#arrow-dep)' : 'url(#arrow)',
    });
    if (e.kind === 'DependsOn') p.setAttribute('stroke-dasharray', '5,3');
    eg.appendChild(p);

    if (hi) {
      const lx = (sp.x + ep.x) / 2, ly = my;
      const viaShort = e.via.length > 20 ? e.via.slice(0, 18) + '\u2026' : e.via;
      const bw = viaShort.length * 5.6 + 12;
      eg.appendChild(attrs(svgEl('rect'), { x: lx - bw / 2, y: ly - 9, width: bw, height: 16, rx: 3, fill: '#0d1117', stroke: '#f0c67440', 'stroke-width': 1 }));
      const lt = attrs(svgEl('text'), { x: lx, y: ly + 3, 'text-anchor': 'middle', 'font-size': 9, fill: '#f0c674', 'font-family': 'monospace' });
      lt.textContent = viaShort;
      eg.appendChild(lt);
    }
  });

  // Nodes
  simNodes.forEach(nd => {
    const isHov = nd === hoveredNode, isSel = nd === selectedNode;
    const isExp = expandedNodes.has(nd.id);
    const dim = hoveredNode && nd !== hoveredNode && !connIds.has(nd.id);
    const col = CATEGORY_COLOR[nd.category] || '#6e7681';
    const nh = nodeHeight(nd);
    const g = attrs(svgEl('g'), { transform: `translate(${nd.x - NODE_W / 2},${nd.y - nh / 2})`, opacity: dim ? 0.18 : 1 });
    g.style.cursor = 'pointer';

    // Main background rect (full height when expanded)
    g.appendChild(attrs(svgEl('rect'), {
      width: NODE_W, height: nh, rx: 7,
      fill: isSel ? '#1f2937' : '#161b22',
      stroke: isSel ? '#f0c674' : isExp ? col : isHov ? col : '#30363d',
      'stroke-width': isSel || isExp || isHov ? 2 : 1,
    }));
    // Left color stripe (full height)
    g.appendChild(attrs(svgEl('rect'), { x: 0, y: 0, width: 4, height: nh, rx: 3, fill: col }));

    const nameEl = attrs(svgEl('text'), { x: 13, y: 20, 'font-size': 11, 'font-weight': 700, fill: '#ffa657', 'font-family': 'monospace' });
    nameEl.textContent = nd.label.length > 28 ? nd.label.slice(0, 26) + '\u2026' : nd.label;
    g.appendChild(nameEl);

    const typeShort = nd.type.split('::').slice(1).join('::');
    const typeEl = attrs(svgEl('text'), { x: 13, y: 34, 'font-size': 9, fill: '#6e7681', 'font-family': 'monospace' });
    typeEl.textContent = typeShort.length > 27 ? typeShort.slice(0, 25) + '\u2026' : typeShort;
    g.appendChild(typeEl);

    let badgeY = 7;
    if (nd.condition) {
      g.appendChild(attrs(svgEl('rect'), { x: NODE_W - 43, y: badgeY, width: 35, height: 13, rx: 3, fill: '#d2a8ff15', stroke: '#d2a8ff50', 'stroke-width': 0.8 }));
      const ct = attrs(svgEl('text'), { x: NODE_W - 25, y: badgeY + 9, 'text-anchor': 'middle', 'font-size': 7, fill: '#d2a8ff', 'font-family': 'monospace' });
      ct.textContent = 'cond';
      g.appendChild(ct);
      badgeY += 15;
    }
    if (nd.deletion_policy === 'Retain' || nd.deletion_policy === 'Snapshot') {
      g.appendChild(attrs(svgEl('rect'), { x: NODE_W - 43, y: badgeY, width: 35, height: 13, rx: 3, fill: '#ff7b7215', stroke: '#ff7b7250', 'stroke-width': 0.8 }));
      const dp = attrs(svgEl('text'), { x: NODE_W - 25, y: badgeY + 9, 'text-anchor': 'middle', 'font-size': 7, fill: '#ff7b72', 'font-family': 'monospace' });
      dp.textContent = nd.deletion_policy === 'Retain' ? 'retain' : 'snap';
      g.appendChild(dp);
    }

    // Expand toggle chevron (bottom-right of header)
    if (nd.props && nd.props.length > 0) {
      const chev = attrs(svgEl('text'), { x: NODE_W - 10, y: 32, 'font-size': 9, fill: '#484f58', 'font-family': 'monospace', 'text-anchor': 'middle' });
      chev.textContent = isExp ? '\u25b4' : '\u25be'; // ▴ / ▾
      g.appendChild(chev);
    }

    // Expanded props section
    if (isExp && nd.props && nd.props.length > 0) {
      // Divider
      g.appendChild(attrs(svgEl('line'), {
        x1: 8, y1: NODE_H + 1, x2: NODE_W - 8, y2: NODE_H + 1,
        stroke: '#30363d', 'stroke-width': 0.8,
      }));
      nd.props.forEach((p, i) => {
        const py = NODE_H + PROP_PAD_TOP + i * PROP_ROW_H;
        const keyEl = attrs(svgEl('text'), { x: 10, y: py + 10, 'font-size': 9, fill: '#79c0ff', 'font-family': 'monospace' });
        keyEl.textContent = p.key.length > 13 ? p.key.slice(0, 12) + '\u2026' : p.key;
        g.appendChild(keyEl);
        const valEl = attrs(svgEl('text'), { x: 84, y: py + 10, 'font-size': 9, fill: '#a5d6ff', 'font-family': 'monospace' });
        valEl.textContent = p.value.length > 19 ? p.value.slice(0, 18) + '\u2026' : p.value;
        g.appendChild(valEl);
      });
    }

    g.addEventListener('mouseenter', () => { hoveredNode = nd; scheduleHoverRender(); });
    g.addEventListener('mouseleave', () => { hoveredNode = null; scheduleHoverRender(); });
    g.addEventListener('click', () => {
      if (nd.props && nd.props.length > 0) {
        if (expandedNodes.has(nd.id)) expandedNodes.delete(nd.id);
        else expandedNodes.add(nd.id);
        render();
      }
      showDetail(nd);
    });
    ng.appendChild(g);
  });
}

// ── Detail panel ──────────────────────────────────────────────────────────
function showDetail(nd) {
  selectedNode = nd;
  const col = CATEGORY_COLOR[nd.category] || '#6e7681';
  const out = simEdges.filter(e => e.source === nd);
  const inc = simEdges.filter(e => e.target === nd);
  const panel = document.getElementById('detail-panel');
  panel.style.display = 'block';
  panel.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;">
      <div style="display:flex;align-items:flex-start;gap:8px;">
        <div style="width:10px;height:10px;border-radius:50%;background:${col};flex-shrink:0;margin-top:2px;"></div>
        <div style="color:#ffa657;font-weight:700;font-size:11px;line-height:1.4;word-break:break-all;">${nd.label}</div>
      </div>
      <button onclick="document.getElementById('detail-panel').style.display='none';selectedNode=null;render();"
        style="background:none;border:none;color:#8b949e;cursor:pointer;font-size:16px;line-height:1;flex-shrink:0;margin-left:6px;">&#215;</button>
    </div>
    <div style="color:#d2a8ff;font-size:9.5px;margin-bottom:8px;line-height:1.5;">${nd.type}</div>
    ${nd.condition ? `<div style="color:#8b949e;font-size:9.5px;margin-bottom:4px;">Condition: <span style="color:#d2a8ff">${nd.condition}</span></div>` : ''}
    ${nd.deletion_policy ? `<div style="color:#8b949e;font-size:9.5px;margin-bottom:8px;">DeletionPolicy: <span style="color:#ff7b72">${nd.deletion_policy}</span></div>` : ''}
    ${out.length > 0 ? `<div style="color:#8b949e;font-size:9px;text-transform:uppercase;letter-spacing:.5px;margin:10px 0 4px;">References \u2192</div>
      ${out.map(e => `<div style="padding:3px 0;border-bottom:1px solid #21262d;">
        <span style="color:#79c0ff;font-size:10px;">\u2192 ${e.target.label}</span><br>
        <span style="color:#8b949e;font-size:9px;">${e.via}</span></div>`).join('')}` : ''}
    ${inc.length > 0 ? `<div style="color:#8b949e;font-size:9px;text-transform:uppercase;letter-spacing:.5px;margin:10px 0 4px;">Referenced by \u2190</div>
      ${inc.map(e => `<div style="padding:3px 0;border-bottom:1px solid #21262d;">
        <span style="color:#a5d6ff;font-size:10px;">\u2190 ${e.source.label}</span><br>
        <span style="color:#8b949e;font-size:9px;">${e.via}</span></div>`).join('')}` : ''}
  `;
  render();
}

// ── Init ──────────────────────────────────────────────────────────────────
function init() {
  const tabs = document.getElementById('tabs');
  GRAPH.templates.forEach((t, i) => {
    const btn = document.createElement('button');
    btn.className = 'tab' + (i === 0 ? ' active' : '');
    btn.textContent = t.name;
    btn.onclick = () => {
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadTemplate(t);
    };
    tabs.appendChild(btn);
  });

  const leg = document.getElementById('legend');
  Object.entries(CATEGORY_COLOR).forEach(([cat, col]) => {
    const d = document.createElement('div');
    d.className = 'legend-item';
    d.innerHTML = `<div class="legend-dot" style="background:${col}"></div>${cat}`;
    leg.appendChild(d);
  });

  if (GRAPH.templates.length > 0) loadTemplate(GRAPH.templates[0]);
}

init();
""")
    L("</script>")
    L("</body>")
    L("</html>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="cfcat",
        description="Catalog CloudFormation templates into a structured text summary.",
    )
    parser.add_argument(
        "targets",
        nargs="+",
        metavar="FILE_OR_DIR",
        help="CFN file(s) or director(ies) to scan",
    )
    parser.add_argument(
        "-o", "--output",
        default="cfcat-output.txt",
        metavar="FILE",
        help="Output file path (default: cfcat-output.txt)",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Skip the global infrastructure summary section",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress to stdout",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"cfcat {VERSION}",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Write an interactive wiring diagram alongside the text output (same base name, .html extension)",
    )
    parser.add_argument(
        "--docs",
        action="store_true",
        help=(
            "Write a Markdown README documenting the stack (same base name, .md extension). "
            "Includes an AI prompt context block ready to paste into Claude or Copilot. "
            "Run as a git pre-commit hook to keep docs passively in sync."
        ),
    )

    args = parser.parse_args()

    if not YAML_AVAILABLE:
        print("[warn] PyYAML not found — YAML files will be skipped. Install with: pip install pyyaml", file=sys.stderr)

    # Discover files
    files = find_cfn_files(args.targets)
    if not files:
        print("No CloudFormation files found.", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"Found {len(files)} candidate file(s).")

    # Parse and format
    output_lines = []
    parsed_templates = []
    skipped = 0

    for f in files:
        if args.verbose:
            print(f"  Parsing {f} ...")

        data, err = load_template(f)
        if err:
            print(f"[skip] {f}: {err}", file=sys.stderr)
            skipped += 1
            continue

        output_lines.extend(format_template(f, data))
        output_lines.append("")
        parsed_templates.append((f, data))

    if not parsed_templates:
        print("No valid CloudFormation templates found.", file=sys.stderr)
        sys.exit(1)

    # Global summary
    if not args.no_summary:
        output_lines.extend(global_summary(parsed_templates))

    # Write output
    out_path = Path(args.output)
    try:
        out_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"[error] Could not write output: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"cfcat: wrote {len(parsed_templates)} template(s) to {out_path}")
    if skipped:
        print(f"       ({skipped} file(s) skipped — see warnings above)")

    # HTML wiring diagram (derives path from text output path)
    if args.html:
        html_path = out_path.with_suffix(".html")
        try:
            html_path.write_text(generate_html_diagram(parsed_templates), encoding="utf-8")
            print(f"cfcat: wrote diagram  to {html_path}")
        except OSError as e:
            print(f"[error] Could not write HTML diagram: {e}", file=sys.stderr)

    # Markdown docs / README (derives path from text output path)
    if args.docs:
        md_path  = out_path.with_suffix(".md")
        # Reconstruct the command so the regen hint in the doc is accurate
        targets_str = " ".join(str(t) for t in args.targets)
        regen_cmd   = f"cfcat {targets_str} --docs -o {args.output}"
        try:
            md_path.write_text(generate_docs(parsed_templates, regen_cmd=regen_cmd), encoding="utf-8")
            print(f"cfcat: wrote docs     to {md_path}")
        except OSError as e:
            print(f"[error] Could not write docs: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
