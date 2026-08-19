package storage

import (
	"testing"
	"time"

	"telegram-emulator/internal/maxbot/models"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	gormlogger "gorm.io/gorm/logger"
)

func newTestStorage(t *testing.T) *Storage {
	t.Helper()

	db, err := gorm.Open(sqlite.Open("file::memory:"), &gorm.Config{
		Logger: gormlogger.Default.LogMode(gormlogger.Silent),
	})
	if err != nil {
		t.Fatalf("не удалось открыть тестовую базу: %v", err)
	}

	store, err := New(db)
	if err != nil {
		t.Fatalf("не удалось подготовить базу: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Logf("ошибка закрытия базы: %v", err)
		}
	})

	return store
}

func TestDraftLifecycle(t *testing.T) {
	store := newTestStorage(t)

	draft, err := store.Draft(42)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if draft != nil {
		t.Fatal("у нового пользователя черновика нет")
	}

	app := &models.Application{UserID: 42, Channel: "max", Status: models.StatusDraft, State: "consent"}
	if err := store.Create(app); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}

	draft, err = store.Draft(42)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if draft == nil || draft.ID != app.ID {
		t.Fatal("черновик должен находиться по идентификатору пользователя")
	}

	app.Status = models.StatusSubmitted
	app.PublicID = "MAX-20260819-0001"
	if err := store.Save(app); err != nil {
		t.Fatalf("ошибка сохранения обращения: %v", err)
	}

	draft, err = store.Draft(42)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if draft != nil {
		t.Fatal("после отправки черновик не возвращается")
	}

	found, err := store.ByPublicID("MAX-20260819-0001")
	if err != nil {
		t.Fatalf("ошибка поиска по номеру: %v", err)
	}
	if found == nil {
		t.Fatal("обращение должно находиться по публичному номеру")
	}

	repeat, err := store.HasClosedApplications(42)
	if err != nil {
		t.Fatalf("ошибка проверки прошлых обращений: %v", err)
	}
	if !repeat {
		t.Fatal("повторное обращение должно определяться по прошлым карточкам")
	}
}

func TestNextPublicIDIncrementsWithinDay(t *testing.T) {
	store := newTestStorage(t)
	day := time.Date(2026, 8, 19, 10, 0, 0, 0, time.UTC)

	first, err := store.NextPublicID(day)
	if err != nil {
		t.Fatalf("ошибка нумерации: %v", err)
	}
	if first != "MAX-20260819-0001" {
		t.Fatalf("неверный номер первого обращения: %q", first)
	}

	if err := store.Create(&models.Application{UserID: 1, PublicID: first, Status: models.StatusSubmitted}); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}

	second, err := store.NextPublicID(day)
	if err != nil {
		t.Fatalf("ошибка нумерации: %v", err)
	}
	if second != "MAX-20260819-0002" {
		t.Fatalf("номер должен увеличиваться в пределах дня, получено %q", second)
	}

	nextDay, err := store.NextPublicID(day.AddDate(0, 0, 1))
	if err != nil {
		t.Fatalf("ошибка нумерации: %v", err)
	}
	if nextDay != "MAX-20260820-0001" {
		t.Fatalf("нумерация должна начинаться заново каждый день, получено %q", nextDay)
	}
}

func TestEventsJournal(t *testing.T) {
	store := newTestStorage(t)

	app := &models.Application{UserID: 7, Status: models.StatusDraft}
	if err := store.Create(app); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}

	if err := store.LogEvent(app.ID, "intake_started", "начато заполнение"); err != nil {
		t.Fatalf("ошибка записи журнала: %v", err)
	}
	if err := store.LogEvent(app.ID, "submitted", "передано координатору"); err != nil {
		t.Fatalf("ошибка записи журнала: %v", err)
	}

	events, err := store.Events(app.ID)
	if err != nil {
		t.Fatalf("ошибка выборки журнала: %v", err)
	}
	if len(events) != 2 || events[0].Type != "intake_started" {
		t.Fatalf("журнал должен восстанавливать историю случая: %+v", events)
	}
}
