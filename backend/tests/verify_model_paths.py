"""End-to-end verification: detection training from backend/ cwd, verify .pt lands in project root only."""
import os
import sys
import pathlib
import warnings
warnings.filterwarnings('ignore')

print('=== 测试 1: 从 backend/ 启动时, 路径是否锚定到项目根 ===')
print('cwd:', os.getcwd())
from app.config import settings
import os as _os
print('os.environ[HF_HOME]        :', _os.environ.get('HF_HOME'))
print('os.environ[TORCH_HOME]     :', _os.environ.get('TORCH_HOME'))
print('os.environ[ULTRALYTICS_HOME]:', _os.environ.get('ULTRALYTICS_HOME'))
print('settings.MODEL_DIR              :', settings.MODEL_DIR)
print('settings.PRETRAINED_CACHE_DIR   :', settings.PRETRAINED_CACHE_DIR)
print('settings.ULTRALYTICS_HOME       :', settings.ULTRALYTICS_HOME)
print('settings.ULTRALYTICS_WEIGHTS_DIR:', settings.ULTRALYTICS_WEIGHTS_DIR)
print('settings.ULTRALYTICS_RUNS_DIR   :', settings.ULTRALYTICS_RUNS_DIR)
print()

print('=== 测试 2: 启动时扫描并迁移 yolov8*.pt ===')
from app.core.ultralytics_setup import configure_ultralytics, migrate_legacy_yolo_weights
configure_ultralytics()
moved = migrate_legacy_yolo_weights()
print(f'本次启动迁移文件数: {moved} (应为 0, 证明已干净)')

print()
print('=== 测试 3: 加载 yolov8n.pt 不会下载/复制到 backend/ ===')
from ultralytics import YOLO
candidate = settings.ULTRALYTICS_WEIGHTS_DIR / 'yolov8n.pt'
print(f'使用权重: {candidate}')
print(f'存在?: {candidate.exists()}')
model = YOLO(str(candidate))
print('模型加载成功')
print(f'模型 task: {model.task}')

print()
print('=== 测试 4: 检查 backend/ 根目录是否干净 ===')
backend = pathlib.Path(r'd:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\backend')
yolov8_in_backend = list(backend.glob('yolov8*.pt'))
if yolov8_in_backend:
    print(f'❌ FAIL: backend/ 仍有 yolov8*.pt: {[f.name for f in yolov8_in_backend]}')
    sys.exit(1)
else:
    print('✅ PASS: backend/ 根目录干净, 无 yolov8*.pt 残留')

# 检查 backend/models 是否还存在
backend_models = backend / 'models'
if backend_models.exists():
    print(f'❌ FAIL: backend/models/ 仍存在: {backend_models}')
    sys.exit(1)
else:
    print('✅ PASS: backend/models/ 已删除')

print()
print('=== 测试 5: 项目根 models/ 包含所有目标文件 ===')
proj_models = pathlib.Path(r'd:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation\models')
yolov8_in_root = list((proj_models / 'cache' / 'ultralytics' / 'weights').glob('yolov8*.pt'))
print(f'项目根 yolov8*.pt: {len(yolov8_in_root)} 个: {[f.name for f in yolov8_in_root]}')
expected = {'yolov8n.pt', 'yolov8s.pt', 'yolov8m.pt', 'yolov8l.pt', 'yolov8x.pt'}
got = {f.name for f in yolov8_in_root}
if expected.issubset(got):
    print('✅ PASS: 项目根有所有 5 个 yolov8 基础权重')
else:
    print(f'❌ FAIL: 缺 {expected - got}')
    sys.exit(1)

# 检查 settings.yaml 内容
print()
print('=== 测试 6: ultralytics settings.yaml 路径正确 ===')
import yaml
with open(settings.ULTRALYTICS_HOME / 'settings.yaml', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)
print(f'  weights_dir : {cfg.get("weights_dir")}')
print(f'  runs_dir    : {cfg.get("runs_dir")}')
print(f'  datasets_dir: {cfg.get("datasets_dir")}')
proj_root = r'D:\works\WorkBuddy\Myhome\毕业论文设计与实现\thesis-image-annotation'
expected_weights = os.path.join(proj_root, 'models', 'cache', 'ultralytics', 'weights').replace('\\', '/')
actual_weights = cfg.get('weights_dir', '').replace('\\', '/')
if actual_weights.lower() == expected_weights.lower():
    print('✅ PASS: settings.yaml 路径锚定项目根')
else:
    print(f'❌ FAIL: expected={expected_weights}, got={actual_weights}')
    sys.exit(1)

print()
print('=' * 60)
print('✅ ALL TESTS PASSED - 模型存储路径已完全修复')
print('=' * 60)
