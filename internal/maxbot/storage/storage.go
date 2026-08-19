// Package storage хранит карточки обращений чат-бота «Точка входа».
package storage

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"telegram-emulator/internal/maxbot/models"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
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

	if dir := filepath.Dir(path); dir != "." && dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("ошибка создания директории базы данных: %w", err)
		}
	}

	db, err := gorm.Open(sqlite.Open(path), &gorm.Config{
		Logger: gormlogger.Default.LogMode(gormlogger.Silent),
	})
	if err != nil {
		return nil, fmt.Errorf("ошибка открытия базы данных: %w", err)
	}

	return New(db)
}

// New создаёт репозиторий поверх готового подключения и применяет миграции
func New(db *gorm.DB) (*Storage, error) {
	if err := db.AutoMigrate(&models.Application{}, &models.Event{}); err != nil {
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
		if err == gorm.ErrRecordNotFound {
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
		if err == gorm.ErrRecordNotFound {
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

// HasClosedApplications сообщает, обращался ли пользователь ранее
func (s *Storage) HasClosedApplications(userID int64) (bool, error) {
	var count int64
	err := s.db.Model(&models.Application{}).
		Where("user_id = ? AND status <> ?", userID, models.StatusDraft).
		Count(&count).Error
	if err != nil {
		return false, fmt.Errorf("ошибка подсчёта обращений: %w", err)
	}

	return count > 0, nil
}

// NextPublicID формирует публичный номер обращения вида MAX-20260819-0007
func (s *Storage) NextPublicID(now time.Time) (string, error) {
	day := now.Format("20060102")
	prefix := "MAX-" + day + "-"

	var count int64
	err := s.db.Model(&models.Application{}).
		Where("public_id LIKE ?", prefix+"%").Count(&count).Error
	if err != nil {
		return "", fmt.Errorf("ошибка нумерации обращений: %w", err)
	}

	return fmt.Sprintf("%s%04d", prefix, count+1), nil
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
