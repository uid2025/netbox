from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from users.models import User, Token
from extras.validators import CustomValidator


# Helper custom validator for token expiry duration
class TokenExpiryDurationValidator(CustomValidator):
    def validate(self, instance, request=None):
        if instance.expires is None:
            return

        created_date = instance.created
        if created_date is None:
            # For new tokens, 'created' is None before the first save.
            # Use current time as the base for validation.
            created_date = timezone.now()
        
        one_year_after_creation = created_date + relativedelta(years=1)

        if instance.expires > one_year_after_creation:
            self.fail(
                "Token expiry cannot exceed 1 year from its creation date.",
                field='expires'
            )


# Helper custom validator for tests
class TestTokenDescriptionValidator(CustomValidator):
    def validate(self, instance, request=None):
        if instance.description == "FAIL_VALIDATION":
            self.fail("Description cannot be FAIL_VALIDATION.", field='description')


class UserConfigTest(TestCase):

    @classmethod
    def setUpTestData(cls):

        user = User.objects.create_user(username='testuser')
        user.config.data = {
            'a': True,
            'b': {
                'foo': 101,
                'bar': 102,
            },
            'c': {
                'foo': {
                    'x': 201,
                },
                'bar': {
                    'y': 202,
                },
                'baz': {
                    'z': 203,
                }
            }
        }
        user.config.save()

    def test_get(self):
        userconfig = User.objects.get(username='testuser').config

        # Retrieve root and nested values
        self.assertEqual(userconfig.get('a'), True)
        self.assertEqual(userconfig.get('b.foo'), 101)
        self.assertEqual(userconfig.get('c.baz.z'), 203)

        # Invalid values should return None
        self.assertIsNone(userconfig.get('invalid'))
        self.assertIsNone(userconfig.get('a.invalid'))
        self.assertIsNone(userconfig.get('b.foo.invalid'))
        self.assertIsNone(userconfig.get('b.foo.x.invalid'))

        # Invalid values with a provided default should return the default
        self.assertEqual(userconfig.get('invalid', 'DEFAULT'), 'DEFAULT')
        self.assertEqual(userconfig.get('a.invalid', 'DEFAULT'), 'DEFAULT')
        self.assertEqual(userconfig.get('b.foo.invalid', 'DEFAULT'), 'DEFAULT')
        self.assertEqual(userconfig.get('b.foo.x.invalid', 'DEFAULT'), 'DEFAULT')

    def test_all(self):
        userconfig = User.objects.get(username='testuser').config
        flattened_data = {
            'a': True,
            'b.foo': 101,
            'b.bar': 102,
            'c.foo.x': 201,
            'c.bar.y': 202,
            'c.baz.z': 203,
        }

        # Retrieve a flattened dictionary containing all config data
        self.assertEqual(userconfig.all(), flattened_data)

    def test_set(self):
        userconfig = User.objects.get(username='testuser').config

        # Overwrite existing values
        userconfig.set('a', 'abc')
        userconfig.set('c.foo.x', 'abc')
        self.assertEqual(userconfig.data['a'], 'abc')
        self.assertEqual(userconfig.data['c']['foo']['x'], 'abc')

        # Create new values
        userconfig.set('d', 'abc')
        userconfig.set('b.baz', 'abc')
        self.assertEqual(userconfig.data['d'], 'abc')
        self.assertEqual(userconfig.data['b']['baz'], 'abc')

        # Set a value and commit to the database
        userconfig.set('a', 'def', commit=True)

        userconfig.refresh_from_db()
        self.assertEqual(userconfig.data['a'], 'def')

        # Attempt to change a branch node to a leaf node
        with self.assertRaises(TypeError):
            userconfig.set('b', 1)

        # Attempt to change a leaf node to a branch node
        with self.assertRaises(TypeError):
            userconfig.set('a.x', 1)

    def test_clear(self):
        userconfig = User.objects.get(username='testuser').config

        # Clear existing values
        userconfig.clear('a')
        userconfig.clear('b.foo')
        self.assertTrue('a' not in userconfig.data)
        self.assertTrue('foo' not in userconfig.data['b'])
        self.assertEqual(userconfig.data['b']['bar'], 102)

        # Clear a non-existing value; should fail silently
        userconfig.clear('invalid')


class TokenValidationTestCase(TestCase):

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        # Ensure a unique username for each test run if tests run in parallel or are not isolated
        # For this case, 'testuser_token_validation' should be fine as tests are usually isolated.
        cls.user = User.objects.create_user(username='testuser_token_validation')

    def test_token_creation_valid_no_custom_rules(self):
        """
        Test that a token can be created successfully when no custom validation rules are active.
        """
        token = Token(user=self.user, description="FAIL_VALIDATION", key="0123456789abcdef0123456789abcdef01234567")
        token.full_clean()  # Should not raise
        token.save()
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_token_creation_violates_custom_rule(self):
        """
        Test that token creation fails if a custom validation rule is violated.
        """
        validator_path = 'users.tests.test_models.TestTokenDescriptionValidator'
        with override_settings(CUSTOM_VALIDATORS={'users.token': [validator_path]}):
            token = Token(user=self.user, description="FAIL_VALIDATION", key="0123456789abcdef0123456789abcdef0123456_")
            with self.assertRaises(ValidationError) as cm:
                token.full_clean()
            self.assertIn("Description cannot be FAIL_VALIDATION.", str(cm.exception.message_dict['description']))

    def test_token_creation_passes_custom_rule(self):
        """
        Test that token creation succeeds if it passes custom validation rules.
        """
        validator_path = 'users.tests.test_models.TestTokenDescriptionValidator'
        with override_settings(CUSTOM_VALIDATORS={'users.token': [validator_path]}):
            token = Token(user=self.user, description="PASS_VALIDATION", key="1123456789abcdef0123456789abcdef01234567")
            token.full_clean()  # Should not raise
            token.save()
            self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    # Tests for TokenExpiryDurationValidator
    def test_token_expiry_more_than_one_year(self):
        validator_path = 'users.tests.test_models.TokenExpiryDurationValidator'
        with override_settings(CUSTOM_VALIDATORS={'users.token': [validator_path]}):
            created_time = timezone.now() - relativedelta(days=10)
            expires_time = created_time + relativedelta(years=1, days=1)
            token = Token(user=self.user, key=Token.generate_key(), description="Test", created=created_time, expires=expires_time)
            with self.assertRaises(ValidationError) as cm:
                token.full_clean()
            self.assertIn("Token expiry cannot exceed 1 year from its creation date.", cm.exception.message_dict.get('expires', [''])[0])

    def test_token_expiry_exactly_one_year(self):
        validator_path = 'users.tests.test_models.TokenExpiryDurationValidator'
        with override_settings(CUSTOM_VALIDATORS={'users.token': [validator_path]}):
            created_time = timezone.now() - relativedelta(days=10)
            expires_time = created_time + relativedelta(years=1)
            token = Token(user=self.user, key=Token.generate_key(), description="Test", created=created_time, expires=expires_time)
            token.full_clean() 
            token.save()
            self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_token_expiry_less_than_one_year(self):
        validator_path = 'users.tests.test_models.TokenExpiryDurationValidator'
        with override_settings(CUSTOM_VALIDATORS={'users.token': [validator_path]}):
            created_time = timezone.now() - relativedelta(days=10)
            expires_time = created_time + relativedelta(months=6)
            token = Token(user=self.user, key=Token.generate_key(), description="Test", created=created_time, expires=expires_time)
            token.full_clean() 
            token.save()
            self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_token_no_expiry_date_with_duration_validator(self): # Renamed for clarity
        validator_path = 'users.tests.test_models.TokenExpiryDurationValidator'
        with override_settings(CUSTOM_VALIDATORS={'users.token': [validator_path]}):
            token = Token(user=self.user, key=Token.generate_key(), description="Test No Expiry", expires=None)
            token.full_clean() 
            token.save()
            self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_new_token_expiry_more_than_one_year_from_today(self):
        validator_path = 'users.tests.test_models.TokenExpiryDurationValidator'
        with override_settings(CUSTOM_VALIDATORS={'users.token': [validator_path]}):
            expires_time = timezone.now() + relativedelta(years=1, days=1)
            token = Token(user=self.user, key=Token.generate_key(), description="New Token Test", expires=expires_time)
            with self.assertRaises(ValidationError) as cm:
                token.full_clean()
            self.assertIn("Token expiry cannot exceed 1 year from its creation date.", cm.exception.message_dict.get('expires', [''])[0])
            
    def test_new_token_expiry_within_one_year_from_today(self):
        validator_path = 'users.tests.test_models.TokenExpiryDurationValidator'
        with override_settings(CUSTOM_VALIDATORS={'users.token': [validator_path]}):
            expires_time = timezone.now() + relativedelta(months=6)
            token = Token(user=self.user, key=Token.generate_key(), description="New Token Test", expires=expires_time)
            token.full_clean() 
            # Not saving, to ensure 'created' is None during full_clean for this specific test case.

    def test_token_creation_custom_rule_not_configured_for_model(self):
        """
        Test that token creation succeeds if a custom rule is configured for a different model.
        """
        validator_path = 'users.tests.test_models.TestTokenDescriptionValidator'
        # Configure the validator for 'dcim.site' instead of 'users.token'
        with override_settings(CUSTOM_VALIDATORS={'dcim.site': [validator_path]}):
            token = Token(user=self.user, description="FAIL_VALIDATION", key="2123456789abcdef0123456789abcdef01234567")
            token.full_clean()  # Should not raise
            token.save()
            self.assertTrue(Token.objects.filter(pk=token.pk).exists())
