// Package storage хранит карточки обращений чат-бота «Точка входа».
package storage

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"telegram-emulator/internal/maxbot/models"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	gormlogger "gorm.io/gorm/logger"
)

// Storage репозиторий обращений
type Storage struct {
	db *gorm.DB
}

// Open открывает базу данных по URL вида sqlite://data/maxbot.db и применяет миграции
func Open(url string) (*Storage, error) {
	path := strings.TrimPrefix(url, "sqlite://")
	path = strings.TrimPrefix(path, "sqlite:")
	if path == "" {
		return nil, fmt.Errorf("пустой путь к базе данных: %q", url)
	}

	// База содержит персональные данные: каталог и файл доступны только владельцу
	if dir := filepath.Dir(path); dir != "." && dir != "" {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return nil, fmt.Errorf("ошибка создания директории базы данных: %w", err)
		}
		if err := os.Chmod(dir, 0o700); err != nil {
			return nil, fmt.Errorf("ошибка установки прав на директорию базы данных: %w", err)
		}
	}

	// WAL и ожидание блокировки: обращения могут приходить одновременно
	dsn := fmt.Sprintf("file:%s?_busy_timeout=5000&_journal_mode=WAL&_txlock=immediate", path)

	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{
		Logger: gormlogger.Default.LogMode(gormlogger.Silent),
	})
	if err != nil {
		return nil, fmt.Errorf("ошибка открытия базы данных: %w", err)
	}

	store, err := New(db)
	if err != nil {
		return nil, err
	}

	if err := os.Chmod(path, 0o600); err != nil {
		return nil, fmt.Errorf("ошибка установки прав на файл базы данных: %w", err)
	}

	return store, nil
}

// New создаёт репозиторий поверх готового подключения и применяет миграции
func New(db *gorm.DB) (*Storage, error) {
	// SQLite допускает одного писателя: пул из одного соединения
	// сериализует транзакции внутри процесса вместо ошибки «database is locked»
	sqlDB, err := db.DB()
	if err != nil {
		return nil, fmt.Errorf("ошибка доступа к подключению: %w", err)
	}
	sqlDB.SetMaxOpenConns(1)
	sqlDB.SetMaxIdleConns(1)

	if err := db.AutoMigrate(
		&models.Application{},
		&models.Event{},
		&models.Action{},
		&models.ProcessedEvent{},
		&models.DailySequence{},
	); err != nil {
		return nil, fmt.Errorf("ошибка миграции базы данных: %w", err)
	}

	return &Storage{db: db}, nil
}

// DB возвращает подключение к базе данных
func (s *Storage) DB() *gorm.DB {
	return s.db
}

// Draft возвращает незавершённую анкету пользователя, если она есть
func (s *Storage) Draft(userID int64) (*models.Application, error) {
	var app models.Application
	err := s.db.Where("user_id = ? AND status = ?", userID, models.StatusDraft).
		Order("id DESC").First(&app).Error
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, nil
		}

		return nil, fmt.Errorf("ошибка поиска черновика обращения: %w", err)
	}

	return &app, nil
}

// Create сохраняет новую карточку обращения
func (s *Storage) Create(app *models.Application) error {
	if err := s.db.Create(app).Error; err != nil {
		return fmt.Errorf("ошибка создания обращения: %w", err)
	}

	return nil
}

// Save обновляет карточку обращения
func (s *Storage) Save(app *models.Application) error {
	if err := s.db.Save(app).Error; err != nil {
		return fmt.Errorf("ошибка сохранения обращения: %w", err)
	}

	return nil
}

// ByPublicID возвращает обращение по публичному номеру
func (s *Storage) ByPublicID(publicID string) (*models.Application, error) {
	var app models.Application
	err := s.db.Where("public_id = ?", publicID).First(&app).Error
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, nil
		}

		return nil, fmt.Errorf("ошибка поиска обращения: %w", err)
	}

	return &app, nil
}

// ByUser возвращает последние обращения пользователя
func (s *Storage) ByUser(userID int64, limit int) ([]models.Application, error) {
	var apps []models.Application
	err := s.db.Where("user_id = ? AND status <> ?", userID, models.StatusDraft).
		Order("id DESC").Limit(limit).Find(&apps).Error
	if err != nil {
		return nil, fmt.Errorf("ошибка выборки обращений пользователя: %w", err)
	}

	return apps, nil
}

// Recent возвращает последние поданные обращения
func (s *Storage) Recent(limit int) ([]models.Application, error) {
	var apps []models.Application
	err := s.db.Where("status <> ?", models.StatusDraft).
		Order("id DESC").Limit(limit).Find(&apps).Error
	if err != nil {
		return nil, fmt.Errorf("ошибка выборки обращений: %w", err)
	}

	return apps, nil
}

// PreviousApplications возвращает ранее поданные обращения того же
// пользователя мессенджера — основу для определения повторности
func (s *Storage) PreviousApplications(userID int64, limit int) ([]models.Application, error) {
	var apps []models.Application
	err := s.db.Where("user_id = ? AND status <> ?", userID, models.StatusDraft).
		Order("id DESC").Limit(limit).Find(&apps).Error
	if err != nil {
		return nil, fmt.Errorf("ошибка выборки прошлых обращений: %w", err)
	}

	return apps, nil
}

// CreateAction сохраняет действие по случаю
func (s *Storage) CreateAction(action *models.Action) error {
	if err := s.db.Create(action).Error; err != nil {
		return fmt.Errorf("ошибка создания действия: %w", err)
	}

	return nil
}

// OpenActions возвращает незавершённые действия в порядке срока
func (s *Storage) OpenActions(limit int) ([]models.Action, error) {
	var actions []models.Action
	err := s.db.Where("status = ?", models.ActionStatusOpen).
		Order("due_at ASC").Limit(limit).Find(&actions).Error
	if err != nil {
		return nil, fmt.Errorf("ошибка выборки действий: %w", err)
	}

	return actions, nil
}

// DueActions возвращает действия, срок которых наступил и о которых
// координатору ещё не напоминали позже указанного момента
func (s *Storage) DueActions(now time.Time, remindedBefore time.Time, limit int) ([]models.Action, error) {
	var actions []models.Action
	err := s.db.Where("status = ? AND due_at <= ? AND (reminded_at IS NULL OR reminded_at < ?)",
		models.ActionStatusOpen, now, remindedBefore).
		Order("due_at ASC").Limit(limit).Find(&actions).Error
	if err != nil {
		return nil, fmt.Errorf("ошибка выборки наступивших действий: %w", err)
	}

	return actions, nil
}

// ActionsByApplication возвращает действия по обращению
func (s *Storage) ActionsByApplication(applicationID uint) ([]models.Action, error) {
	var actions []models.Action
	err := s.db.Where("application_id = ?", applicationID).Order("due_at ASC").Find(&actions).Error
	if err != nil {
		return nil, fmt.Errorf("ошибка выборки действий по обращению: %w", err)
	}

	return actions, nil
}

// ActionByID возвращает действие по идентификатору
func (s *Storage) ActionByID(id uint) (*models.Action, error) {
	var action models.Action
	err := s.db.Where("id = ?", id).First(&action).Error
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, nil
		}

		return nil, fmt.Errorf("ошибка поиска действия: %w", err)
	}

	return &action, nil
}

// SaveAction обновляет действие
func (s *Storage) SaveAction(action *models.Action) error {
	if err := s.db.Save(action).Error; err != nil {
		return fmt.Errorf("ошибка сохранения действия: %w", err)
	}

	return nil
}

// MarkReminded отмечает, что о действии напомнили
func (s *Storage) MarkReminded(action *models.Action, at time.Time) error {
	action.RemindedAt = &at

	return s.SaveAction(action)
}

// MarkProcessed отмечает событие обработанным и сообщает, было ли оно новым.
//
// Повторная доставка того же обновления возвращает false: обработчик его пропустит.
func (s *Storage) MarkProcessed(key string) (bool, error) {
	err := s.db.Create(&models.ProcessedEvent{Key: key}).Error
	if err == nil {
		return true, nil
	}

	var exists int64
	if countErr := s.db.Model(&models.ProcessedEvent{}).Where("key = ?", key).Count(&exists).Error; countErr == nil && exists > 0 {
		return false, nil
	}

	return false, fmt.Errorf("ошибка отметки обработанного события: %w", err)
}

// CleanupProcessed удаляет старые отметки об обработанных событиях
func (s *Storage) CleanupProcessed(before time.Time) error {
	if err := s.db.Where("created_at < ?", before).Delete(&models.ProcessedEvent{}).Error; err != nil {
		return fmt.Errorf("ошибка очистки отметок событий: %w", err)
	}

	return nil
}

// NextPublicID выдаёт публичный номер обращения вида MAX-20260819-0007.
//
// Номер выдаётся атомарно из дневного счётчика: два обращения, поступивших
// одновременно, никогда не получают одинаковый номер.
func (s *Storage) NextPublicID(now time.Time) (string, error) {
	day := now.Format("20060102")
	number := 0

	err := s.db.Transaction(func(tx *gorm.DB) error {
		sequence := &models.DailySequence{}
		err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
			Where("day = ?", day).First(sequence).Error

		switch {
		case err == nil:
			sequence.NextNumber++
			if err := tx.Save(sequence).Error; err != nil {
				return err
			}
		case errors.Is(err, gorm.ErrRecordNotFound):
			sequence = &models.DailySequence{Day: day, NextNumber: 1}
			if err := tx.Create(sequence).Error; err != nil {
				return err
			}
		default:
			return err
		}

		number = sequence.NextNumber

		return nil
	})
	if err != nil {
		return "", fmt.Errorf("ошибка нумерации обращений: %w", err)
	}

	return fmt.Sprintf("MAX-%s-%04d", day, number), nil
}

// LogEvent добавляет запись в журнал обращения
func (s *Storage) LogEvent(applicationID uint, eventType, details string) error {
	event := &models.Event{
		ApplicationID: applicationID,
		Type:          eventType,
		Details:       details,
	}
	if err := s.db.Create(event).Error; err != nil {
		return fmt.Errorf("ошибка записи журнала: %w", err)
	}

	return nil
}

// Events возвращает журнал по обращению
func (s *Storage) Events(applicationID uint) ([]models.Event, error) {
	var events []models.Event
	err := s.db.Where("application_id = ?", applicationID).Order("id ASC").Find(&events).Error
	if err != nil {
		return nil, fmt.Errorf("ошибка выборки журнала: %w", err)
	}

	return events, nil
}

// Close закрывает подключение к базе данных
func (s *Storage) Close() error {
	sqlDB, err := s.db.DB()
	if err != nil {
		return err
	}

	return sqlDB.Close()
}
