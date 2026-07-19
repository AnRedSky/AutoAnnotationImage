-- =============================================================================
-- v2.0.0 数据库迁移 SQL (MySQL 5.7+ / 8.0+)
-- =============================================================================
-- 用途: 补齐 v1.0.0 已有表中 v2.0.0 新加的列
-- 适用: 已用 v1.0.0 部署, 现升级到 v2.0.0 的 MySQL 实例
-- 用法: mysql -u root -p your_db < migrate_v2_0_0.sql
-- 幂等: 重复执行不会破坏数据 (存储过程会先检查列是否存在)
-- =============================================================================

-- 切换到目标数据库 (按你的实际库名修改, 或者注释掉手动 use)
-- USE thesis_annotation;

DELIMITER $$

DROP PROCEDURE IF EXISTS migrate_v2_0_0$$

CREATE PROCEDURE migrate_v2_0_0()
BEGIN
    -- ============== 1. training_jobs.task_type ==============
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'training_jobs'
          AND COLUMN_NAME = 'task_type'
    ) THEN
        ALTER TABLE `training_jobs`
            ADD COLUMN `task_type` VARCHAR(32) NOT NULL DEFAULT 'classification';
        ALTER TABLE `training_jobs` ADD INDEX `ix_training_jobs_task_type` (`task_type`);
        SELECT '[add] training_jobs.task_type' AS log;
    ELSE
        SELECT '[skip] training_jobs.task_type: already exists' AS log;
    END IF;

    -- ============== 2. model_version.task_type ==============
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'model_version'
          AND COLUMN_NAME = 'task_type'
    ) THEN
        ALTER TABLE `model_version`
            ADD COLUMN `task_type` VARCHAR(32) NOT NULL DEFAULT 'classification';
        ALTER TABLE `model_version` ADD INDEX `ix_model_version_task_type` (`task_type`);
        SELECT '[add] model_version.task_type' AS log;
    ELSE
        SELECT '[skip] model_version.task_type: already exists' AS log;
    END IF;

    -- ============== 3. model_version.map_50 ==============
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'model_version'
          AND COLUMN_NAME = 'map_50'
    ) THEN
        ALTER TABLE `model_version` ADD COLUMN `map_50` FLOAT NULL;
        SELECT '[add] model_version.map_50' AS log;
    ELSE
        SELECT '[skip] model_version.map_50: already exists' AS log;
    END IF;

    -- ============== 4. model_version.map_50_95 ==============
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'model_version'
          AND COLUMN_NAME = 'map_50_95'
    ) THEN
        ALTER TABLE `model_version` ADD COLUMN `map_50_95` FLOAT NULL;
        SELECT '[add] model_version.map_50_95' AS log;
    ELSE
        SELECT '[skip] model_version.map_50_95: already exists' AS log;
    END IF;

    -- ============== 5. model_version.miou ==============
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'model_version'
          AND COLUMN_NAME = 'miou'
    ) THEN
        ALTER TABLE `model_version` ADD COLUMN `miou` FLOAT NULL;
        SELECT '[add] model_version.miou' AS log;
    ELSE
        SELECT '[skip] model_version.miou: already exists' AS log;
    END IF;

    -- ============== 6. model_version.pixel_accuracy ==============
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'model_version'
          AND COLUMN_NAME = 'pixel_accuracy'
    ) THEN
        ALTER TABLE `model_version` ADD COLUMN `pixel_accuracy` FLOAT NULL;
        SELECT '[add] model_version.pixel_accuracy' AS log;
    ELSE
        SELECT '[skip] model_version.pixel_accuracy: already exists' AS log;
    END IF;

    -- ============== 7. model_version.dice_score ==============
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'model_version'
          AND COLUMN_NAME = 'dice_score'
    ) THEN
        ALTER TABLE `model_version` ADD COLUMN `dice_score` FLOAT NULL;
        SELECT '[add] model_version.dice_score' AS log;
    ELSE
        SELECT '[skip] model_version.dice_score: already exists' AS log;
    END IF;

    -- ============== 8. image.task_type ==============
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'image'
          AND COLUMN_NAME = 'task_type'
    ) THEN
        ALTER TABLE `image`
            ADD COLUMN `task_type` VARCHAR(32) NOT NULL DEFAULT 'classification';
        ALTER TABLE `image` ADD INDEX `ix_image_task_type` (`task_type`);
        SELECT '[add] image.task_type' AS log;
    ELSE
        SELECT '[skip] image.task_type: already exists' AS log;
    END IF;

    -- ============== 9. 新表: bbox_annotation / segmentation_mask ==============
    -- 这两张是 v2.0.0 全新的表, init_db() 会自动 create_all 建出来.
    -- 如果你之前重启过 FastAPI app 但 init_db 报错 (比如 SQLAlchemy 报错导致未建),
    -- 手动执行以下 CREATE TABLE:

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'bbox_annotation'
    ) THEN
        CREATE TABLE `bbox_annotation` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `image_id` INT NOT NULL,
            `category_id` INT NULL,
            `x_min` FLOAT NOT NULL,
            `y_min` FLOAT NOT NULL,
            `x_max` FLOAT NOT NULL,
            `y_max` FLOAT NOT NULL,
            `confidence` FLOAT NULL,
            `source` VARCHAR(32) NOT NULL DEFAULT 'human',
            `annotated_by` INT NULL,
            `created_at` DATETIME NULL,
            `updated_at` DATETIME NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_bbox_annotation_image_id` (`image_id`),
            INDEX `ix_bbox_annotation_category_id` (`category_id`),
            INDEX `idx_bbox_image_category` (`image_id`, `category_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        SELECT '[add] table bbox_annotation' AS log;
    ELSE
        SELECT '[skip] table bbox_annotation: already exists' AS log;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'segmentation_mask'
    ) THEN
        CREATE TABLE `segmentation_mask` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `image_id` INT NOT NULL,
            `mask_path` VARCHAR(500) NOT NULL,
            `width` INT NOT NULL,
            `height` INT NOT NULL,
            `source` VARCHAR(32) NOT NULL DEFAULT 'human',
            `annotated_by` INT NULL,
            `created_at` DATETIME NULL,
            `updated_at` DATETIME NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `ix_segmentation_mask_image_id` (`image_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        SELECT '[add] table segmentation_mask' AS log;
    ELSE
        SELECT '[skip] table segmentation_mask: already exists' AS log;
    END IF;

    SELECT '== migrate_v2_0_0 完成 ==' AS done;
END$$

DELIMITER ;

-- 一次性调用
CALL migrate_v2_0_0();
